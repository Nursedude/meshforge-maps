#!/usr/bin/env bash
# MeshForge Maps - Uninstall Script
#
# Usage:
#   sudo bash scripts/uninstall.sh [OPTIONS]
#
# Options:
#   --purge    Also remove user data, config, and cache directories
#   --help     Show this help message

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

PURGE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --purge) PURGE=true; shift ;;
        --help|-h)
            head -10 "$0" | tail -7
            exit 0
            ;;
        *)
            err "Unknown option: $1"
            exit 1
            ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    err "This script must be run as root (sudo bash scripts/uninstall.sh)"
    exit 1
fi

REAL_USER="${SUDO_USER:-pi}"
REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)

echo ""
echo "======================================"
echo "  MeshForge Maps Uninstaller"
echo "======================================"
echo ""

# --- Step 1: Stop and disable service ---
if systemctl is-active --quiet meshforge-maps 2>/dev/null; then
    info "Stopping meshforge-maps service..."
    systemctl stop meshforge-maps
    ok "Service stopped"
fi

if systemctl is-enabled --quiet meshforge-maps 2>/dev/null; then
    info "Disabling meshforge-maps service..."
    systemctl disable meshforge-maps
    ok "Service disabled"
fi

# --- Step 2: Remove service file ---
if [[ -f /etc/systemd/system/meshforge-maps.service ]]; then
    info "Removing systemd service file..."
    rm -f /etc/systemd/system/meshforge-maps.service
    systemctl daemon-reload
    ok "Service file removed"
else
    info "No service file found, skipping"
fi

# --- Step 3: Remove installation directory ---
INSTALL_DIR="/opt/meshforge-maps"
if [[ -d "$INSTALL_DIR" ]] && [[ ! -L "$INSTALL_DIR" ]]; then
    info "Removing installation directory: $INSTALL_DIR"
    rm -rf "$INSTALL_DIR"
    ok "Installation directory removed"
elif [[ -L "$INSTALL_DIR" ]]; then
    info "Removing symlink: $INSTALL_DIR"
    rm -f "$INSTALL_DIR"
    ok "Symlink removed (source directory preserved)"
else
    info "No installation directory at $INSTALL_DIR"
fi

# --- Step 4: Optionally remove user data ---
if [[ "$PURGE" == true ]]; then
    warn "Purging user data, config, and cache..."

    CONFIG_DIR="$REAL_HOME/.config/meshforge/plugins/org.meshforge.extension.maps"
    DATA_DIR="$REAL_HOME/.local/share/meshforge"
    CACHE_DIR="$REAL_HOME/.cache/meshforge"

    for dir in "$CONFIG_DIR" "$CACHE_DIR"; do
        if [[ -d "$dir" ]]; then
            rm -rf "$dir"
            ok "Removed: $dir"
        fi
    done

    # Only remove data dir if it contains no files from other meshforge components
    if [[ -d "$DATA_DIR" ]]; then
        warn "Data directory: $DATA_DIR"
        warn "Contains node history and cache files shared with MeshForge core."
        echo -n "  Remove data directory? [y/N]: "
        read -r answer
        if [[ "${answer,,}" == "y" ]]; then
            rm -rf "$DATA_DIR"
            ok "Removed: $DATA_DIR"
        else
            info "Data directory preserved"
        fi
    fi
else
    info "User data preserved (use --purge to remove)"
    info "  Config: $REAL_HOME/.config/meshforge/plugins/org.meshforge.extension.maps/"
    info "  Data:   $REAL_HOME/.local/share/meshforge/"
    info "  Cache:  $REAL_HOME/.cache/meshforge/"
fi

# --- Done ---
echo ""
echo "======================================"
echo -e "  ${GREEN}MeshForge Maps uninstalled${NC}"
echo "======================================"
echo ""
if [[ "$PURGE" != true ]]; then
    echo "  User data was preserved. Run with --purge to remove everything."
fi
echo ""
