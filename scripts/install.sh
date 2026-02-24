#!/usr/bin/env bash
# MeshForge Maps - Installation Script for Raspberry Pi
#
# Supports: Debian Bookworm (12), Trixie (13), Ubuntu 22.04+
# Tested on: Pi Zero 2 W, Pi 3B+, Pi 4, Pi 5
#
# Usage:
#   sudo bash scripts/install.sh [OPTIONS]
#
# Options:
#   --no-radio     Configure for MQTT-only mode (no local radio hardware)
#   --in-place     Use current directory instead of copying to /opt
#   --help         Show this help message

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

INSTALL_DIR="/opt/meshforge-maps"
NO_RADIO=false
IN_PLACE=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-radio)  NO_RADIO=true; shift ;;
        --in-place)  IN_PLACE=true; shift ;;
        --help|-h)
            head -18 "$0" | tail -12
            exit 0
            ;;
        *)
            err "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Must run as root
if [[ $EUID -ne 0 ]]; then
    err "This script must be run as root (sudo bash scripts/install.sh)"
    exit 1
fi

# Detect the real user (not root)
REAL_USER="${SUDO_USER:-pi}"
REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
if [[ -z "$REAL_HOME" ]]; then
    err "Cannot determine home directory for user: $REAL_USER"
    exit 1
fi

echo ""
echo "======================================"
echo "  MeshForge Maps Installer"
echo "======================================"
echo ""

# --- Step 1: Detect OS and Python ---
info "Detecting system..."

OS_ID=$(. /etc/os-release 2>/dev/null && echo "$ID" || echo "unknown")
OS_VERSION=$(. /etc/os-release 2>/dev/null && echo "$VERSION_CODENAME" || echo "unknown")
PYTHON_BIN=""

for py in python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v "$py" &>/dev/null; then
        PYTHON_BIN="$py"
        break
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    err "Python 3.9+ not found. Install with: sudo apt install python3"
    exit 1
fi

PYTHON_VERSION=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$("$PYTHON_BIN" -c "import sys; print(sys.version_info.major)")
PYTHON_MINOR=$("$PYTHON_BIN" -c "import sys; print(sys.version_info.minor)")

if [[ "$PYTHON_MAJOR" -lt 3 ]] || [[ "$PYTHON_MINOR" -lt 9 ]]; then
    err "Python $PYTHON_VERSION detected, but 3.9+ required"
    exit 1
fi

ok "OS: $OS_ID $OS_VERSION | Python: $PYTHON_VERSION ($PYTHON_BIN)"

# --- Step 2: Install system dependencies ---
info "Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3-venv git >/dev/null 2>&1
ok "System packages installed"

# --- Step 3: Copy to install directory ---
if [[ "$IN_PLACE" == true ]]; then
    INSTALL_DIR="$REPO_DIR"
    info "Using in-place installation at $INSTALL_DIR"
else
    if [[ -d "$INSTALL_DIR" ]]; then
        info "Updating existing installation at $INSTALL_DIR"
        # Preserve user settings
        if [[ -f "$INSTALL_DIR/venv/bin/python" ]]; then
            info "Existing venv found, will reuse"
        fi
    else
        info "Installing to $INSTALL_DIR"
    fi
    mkdir -p "$INSTALL_DIR"
    rsync -a --exclude='.git' --exclude='venv' --exclude='__pycache__' \
        --exclude='.pytest_cache' "$REPO_DIR/" "$INSTALL_DIR/"
    ok "Files copied to $INSTALL_DIR"
fi

# --- Step 4: Create Python venv (PEP 668 compliant) ---
VENV_DIR="$INSTALL_DIR/venv"
if [[ ! -f "$VENV_DIR/bin/python" ]]; then
    info "Creating Python virtual environment (PEP 668 / Trixie safe)..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    ok "Virtual environment created"
else
    info "Virtual environment already exists"
fi

# --- Step 5: Install optional dependencies ---
info "Installing optional dependencies..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip 2>/dev/null || true
"$VENV_DIR/bin/pip" install --quiet paho-mqtt websockets 2>/dev/null || true
"$VENV_DIR/bin/pip" install --quiet 'pyopenssl>=25.3.0' 'cryptography>=45.0.7,<47' 2>/dev/null || true
ok "Dependencies installed (paho-mqtt, websockets, pyopenssl, cryptography)"

# --- Step 6: Write no-radio config if requested ---
if [[ "$NO_RADIO" == true ]]; then
    CONFIG_DIR="$REAL_HOME/.config/meshforge/plugins/org.meshforge.extension.maps"
    mkdir -p "$CONFIG_DIR"

    # Only write if no existing config (don't overwrite user settings)
    if [[ ! -f "$CONFIG_DIR/settings.json" ]]; then
        cat > "$CONFIG_DIR/settings.json" <<'SETTINGS'
{
  "enable_meshtastic": true,
  "enable_reticulum": false,
  "enable_hamclock": true,
  "enable_aredn": false,
  "meshtastic_source": "mqtt_only",
  "http_host": "0.0.0.0",
  "ws_host": "0.0.0.0"
}
SETTINGS
        chmod 600 "$CONFIG_DIR/settings.json"
        chown "$REAL_USER:$REAL_USER" "$CONFIG_DIR/settings.json"
        ok "No-radio config written (MQTT + HamClock/NOAA only)"
    else
        warn "Existing settings.json found, not overwriting"
    fi
fi

# --- Step 7: Create data/cache directories ---
for dir in \
    "$REAL_HOME/.config/meshforge" \
    "$REAL_HOME/.local/share/meshforge" \
    "$REAL_HOME/.cache/meshforge/logs"; do
    mkdir -p "$dir"
    chown -R "$REAL_USER:$REAL_USER" "$dir"
done
ok "Data directories created"

# --- Step 8: Install systemd service ---
info "Installing systemd service..."

# Generate service file with correct paths
sed -e "s|User=pi|User=$REAL_USER|g" \
    -e "s|Group=pi|Group=$REAL_USER|g" \
    -e "s|WorkingDirectory=/opt/meshforge-maps|WorkingDirectory=$INSTALL_DIR|g" \
    -e "s|ExecStart=/opt/meshforge-maps|ExecStart=$INSTALL_DIR|g" \
    -e "s|/home/pi|$REAL_HOME|g" \
    "$INSTALL_DIR/scripts/meshforge-maps.service" > /etc/systemd/system/meshforge-maps.service

systemctl daemon-reload
systemctl enable meshforge-maps
ok "Service installed and enabled (meshforge-maps.service)"

# --- Step 9: Set ownership ---
chown -R "$REAL_USER:$REAL_USER" "$INSTALL_DIR"
ok "Ownership set to $REAL_USER"

# --- Done ---
echo ""
echo "======================================"
echo -e "  ${GREEN}Installation complete!${NC}"
echo "======================================"
echo ""
echo "  Start now:     sudo systemctl start meshforge-maps"
echo "  View logs:     journalctl -u meshforge-maps -f"
echo "  Check status:  sudo systemctl status meshforge-maps"
echo "  Web map:       http://$(hostname -I | awk '{print $1}'):8808"
echo "  TUI (SSH):     $INSTALL_DIR/venv/bin/python -m src.main --tui-only"
echo ""
if [[ "$NO_RADIO" == true ]]; then
    echo "  Mode: No-radio (MQTT + HamClock/NOAA)"
    echo "  Meshtastic nodes pulled from public MQTT broker"
fi
echo ""
echo "  Verify:  bash $INSTALL_DIR/scripts/verify.sh"
echo ""
