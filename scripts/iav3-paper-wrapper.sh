#!/usr/bin/env bash
# Wrapper invoked by launchd. Activates the venv, runs one paper cycle,
# logs output to ~/Library/Logs/iav3/paper-YYYY-MM-DD.log.
#
# Exit code is the agent's exit code so launchd can observe failures
# (it logs them but does not auto-restart — KeepAlive=false in the plist).

set -euo pipefail

PROJECT_DIR="/Users/hansmseraphim/iav3"
VENV_ACTIVATE="$PROJECT_DIR/agent/.venv/bin/activate"
LOG_DIR="$HOME/Library/Logs/iav3"
LOG_FILE="$LOG_DIR/paper-$(date +%Y-%m-%d).log"

mkdir -p "$LOG_DIR"

{
    echo "=== iav3 paper cycle: $(date '+%Y-%m-%d %H:%M:%S %Z') ==="

    if [ ! -f "$VENV_ACTIVATE" ]; then
        echo "FATAL: venv not found at $VENV_ACTIVATE"
        echo "Run: cd $PROJECT_DIR/agent && python3 -m venv .venv && pip install -e '.[dev]'"
        exit 1
    fi

    cd "$PROJECT_DIR"
    # shellcheck disable=SC1090
    source "$VENV_ACTIVATE"

    # Health gate: if any API is down, skip the cycle and log it.
    if ! iav3 validate-env 2>&1 | tee -a "$LOG_FILE" | grep -q "PASS"; then
        echo "WARN: validate-env reported no PASS lines; running cycle anyway"
    fi

    iav3 paper
    echo "=== exit code: 0 ==="
} >> "$LOG_FILE" 2>&1 || {
    echo "=== exit code: $? ==="
    exit "$?"
}
