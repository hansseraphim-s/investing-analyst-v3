#!/usr/bin/env bash
# Uninstall the launchd job. After this, the agent runs ONLY when you
# invoke `iav3 paper` manually.

set -euo pipefail

LABEL="com.fairwinds.iav3-paper"
DST_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if launchctl list | grep -q "$LABEL"; then
    launchctl unload "$DST_PLIST" 2>/dev/null || true
    echo "Unloaded: $LABEL"
else
    echo "Not loaded: $LABEL"
fi

if [ -f "$DST_PLIST" ]; then
    rm "$DST_PLIST"
    echo "Removed plist: $DST_PLIST"
fi

echo "Logs remain at ~/Library/Logs/iav3/ — delete manually if you want."
