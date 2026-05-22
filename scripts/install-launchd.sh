#!/usr/bin/env bash
# Install the launchd job that runs `iav3 paper` Mon-Fri at 12:50 PM PT.
#
# Idempotent: re-running this script is safe (it unloads any existing job
# of the same label first, then reloads with the latest plist).

set -euo pipefail

LABEL="com.fairwinds.iav3-paper"
PROJECT_DIR="/Users/hansmseraphim/iav3"
SRC_PLIST="$PROJECT_DIR/scripts/com.fairwinds.iav3-paper.plist"
DST_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
WRAPPER="$PROJECT_DIR/scripts/iav3-paper-wrapper.sh"

if [ ! -f "$SRC_PLIST" ]; then
    echo "FATAL: plist not found at $SRC_PLIST"
    exit 1
fi
if [ ! -f "$WRAPPER" ]; then
    echo "FATAL: wrapper not found at $WRAPPER"
    exit 1
fi

# Make sure wrapper is executable
chmod +x "$WRAPPER"

mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$HOME/Library/Logs/iav3"

# Unload existing job (no-op if none loaded)
if launchctl list | grep -q "$LABEL"; then
    echo "Unloading existing job: $LABEL"
    launchctl unload "$DST_PLIST" 2>/dev/null || true
fi

# Copy fresh plist + load
cp "$SRC_PLIST" "$DST_PLIST"
launchctl load "$DST_PLIST"

# Verify it loaded
if launchctl list | grep -q "$LABEL"; then
    echo "OK: $LABEL is loaded."
else
    echo "WARN: $LABEL was not found in launchctl list after load."
    exit 1
fi

echo ""
echo "Schedule: Monday-Friday at 12:30 PM Pacific (3:30 PM Eastern)."
echo "Logs:     ~/Library/Logs/iav3/paper-YYYY-MM-DD.log"
echo ""
echo "Useful commands:"
echo "  launchctl list | grep $LABEL          # status"
echo "  tail -f ~/Library/Logs/iav3/paper-\$(date +%Y-%m-%d).log"
echo "  bash $PROJECT_DIR/scripts/uninstall-launchd.sh   # stop the schedule"
echo ""
echo "To trigger one cycle manually right now (for testing):"
echo "  launchctl start $LABEL"
