#!/usr/bin/env bash
# MeshForge Maps - Post-Install Verification Script
#
# Based on meshforge parent's verify_post_install.sh pattern.
# Checks Python, venv, service status, ports, and endpoints.
#
# Usage:
#   bash scripts/verify.sh
#
# Exit codes:
#   0 = all checks passed
#   1 = critical failure
#   2 = warnings (non-critical)

set -uo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASS=0
WARN=0
FAIL=0

pass() { echo -e "  ${GREEN}PASS${NC} $*"; ((PASS++)); }
warn() { echo -e "  ${YELLOW}WARN${NC} $*"; ((WARN++)); }
fail() { echo -e "  ${RED}FAIL${NC} $*"; ((FAIL++)); }
section() { echo -e "\n${BLUE}[$1]${NC} $2"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$(dirname "$SCRIPT_DIR")"

echo ""
echo "======================================"
echo "  MeshForge Maps Verification"
echo "======================================"

# --- Section 1: Python ---
section "1/6" "Python Environment"

PYTHON_BIN=""
if [[ -f "$INSTALL_DIR/venv/bin/python" ]]; then
    PYTHON_BIN="$INSTALL_DIR/venv/bin/python"
    pass "Virtual environment found at $INSTALL_DIR/venv/"
else
    # Try system Python
    for py in python3.12 python3.11 python3.10 python3.9 python3; do
        if command -v "$py" &>/dev/null; then
            PYTHON_BIN="$py"
            break
        fi
    done
    warn "No venv found (PEP 668 may require one on Trixie)"
fi

if [[ -n "$PYTHON_BIN" ]]; then
    PY_VER=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
    PY_MINOR=$("$PYTHON_BIN" -c "import sys; print(sys.version_info.minor)" 2>/dev/null)
    if [[ "$PY_MINOR" -ge 9 ]]; then
        pass "Python $PY_VER (>= 3.9 required)"
    else
        fail "Python $PY_VER is below minimum 3.9"
    fi
else
    fail "No Python 3 found"
fi

# --- Section 2: Imports ---
section "2/6" "Module Imports"

if [[ -n "$PYTHON_BIN" ]]; then
    if "$PYTHON_BIN" -c "import src.utils.paths" 2>/dev/null; then
        pass "src.utils.paths imports"
    else
        # Try with PYTHONPATH
        if PYTHONPATH="$INSTALL_DIR" "$PYTHON_BIN" -c "import src.utils.paths" 2>/dev/null; then
            pass "src.utils.paths imports (with PYTHONPATH)"
        else
            fail "src.utils.paths import failed"
        fi
    fi

    if "$PYTHON_BIN" -c "import src.utils.config" 2>/dev/null; then
        pass "src.utils.config imports"
    else
        if PYTHONPATH="$INSTALL_DIR" "$PYTHON_BIN" -c "import src.utils.config" 2>/dev/null; then
            pass "src.utils.config imports (with PYTHONPATH)"
        else
            fail "src.utils.config import failed"
        fi
    fi

    # Optional dependencies
    if "$PYTHON_BIN" -c "import paho.mqtt" 2>/dev/null; then
        pass "paho-mqtt available"
    else
        warn "paho-mqtt not installed (MQTT subscription disabled)"
    fi

    if "$PYTHON_BIN" -c "import websockets" 2>/dev/null; then
        pass "websockets available"
    else
        warn "websockets not installed (real-time updates disabled)"
    fi
fi

# --- Section 3: Service Status ---
section "3/6" "Systemd Service"

if systemctl list-unit-files meshforge-maps.service &>/dev/null; then
    if systemctl is-enabled meshforge-maps.service &>/dev/null; then
        pass "Service enabled (starts on boot)"
    else
        warn "Service installed but not enabled"
    fi

    if systemctl is-active meshforge-maps.service &>/dev/null; then
        pass "Service is running"
    else
        warn "Service is not running (start with: sudo systemctl start meshforge-maps)"
    fi
else
    warn "meshforge-maps.service not installed"
fi

# --- Section 4: Port Availability ---
section "4/6" "Network Ports"

check_port() {
    local port=$1
    local desc=$2
    if command -v ss &>/dev/null; then
        if ss -tlnp 2>/dev/null | grep -q ":${port} "; then
            pass "Port $port ($desc) is listening"
        else
            warn "Port $port ($desc) not listening"
        fi
    elif command -v netstat &>/dev/null; then
        if netstat -tlnp 2>/dev/null | grep -q ":${port} "; then
            pass "Port $port ($desc) is listening"
        else
            warn "Port $port ($desc) not listening"
        fi
    else
        warn "Cannot check port $port (no ss or netstat)"
    fi
}

check_port 8808 "HTTP API"
check_port 8809 "WebSocket"

# --- Section 5: HTTP Endpoint ---
section "5/6" "HTTP API Test"

if command -v curl &>/dev/null; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 "http://localhost:8808/api/status" 2>/dev/null || echo "000")
    if [[ "$HTTP_CODE" == "200" ]]; then
        pass "GET /api/status returned 200"
    elif [[ "$HTTP_CODE" == "000" ]]; then
        warn "Cannot connect to http://localhost:8808 (service may not be running)"
    else
        warn "GET /api/status returned HTTP $HTTP_CODE"
    fi
else
    warn "curl not installed, skipping HTTP test"
fi

# --- Section 6: Data Directories ---
section "6/6" "Filesystem"

REAL_HOME="${HOME}"
if [[ -n "${SUDO_USER:-}" ]]; then
    REAL_HOME=$(eval echo "~$SUDO_USER")
fi

for dir in \
    "$REAL_HOME/.config/meshforge" \
    "$REAL_HOME/.local/share/meshforge" \
    "$REAL_HOME/.cache/meshforge"; do
    if [[ -d "$dir" ]]; then
        pass "$dir exists"
    else
        warn "$dir missing (will be auto-created on first run)"
    fi
done

# --- Summary ---
echo ""
echo "======================================"
TOTAL=$((PASS + WARN + FAIL))
echo -e "  Results: ${GREEN}$PASS passed${NC}, ${YELLOW}$WARN warnings${NC}, ${RED}$FAIL failed${NC} ($TOTAL checks)"
echo "======================================"
echo ""

if [[ $FAIL -gt 0 ]]; then
    exit 1
elif [[ $WARN -gt 0 ]]; then
    exit 2
else
    exit 0
fi
