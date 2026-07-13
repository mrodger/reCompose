#!/usr/bin/env bash
# start_preview.sh - launch the reCompose RM2 live preview as a persistent
# background service, logging to ~/rm2_preview.log.
#
# Usage:
#   ./start_preview.sh                       # latest PDF in the pipeline dir
#   ./start_preview.sh lyzr-platform-analysis-v2   # pin a document
#
# Then open http://localhost:7700 in your browser.
# Stop it with:  pkill -f rm2_preview.py

set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${RM2_PREVIEW_PORT:-7700}"
FILE_ARG="${1:-}"

cd "$HERE"
nohup python3 "$HERE/rm2_preview.py" --port "$PORT" ${FILE_ARG:+--file "$FILE_ARG"} \
    > ~/rm2_preview.log 2>&1 &

echo "reCompose RM2 preview starting on http://localhost:$PORT"
echo "(PID $!) — logs: ~/rm2_preview.log"
sleep 2
if curl -s -o /dev/null "http://localhost:$PORT/api/state"; then
    echo "OK — server is up."
else
    echo "WARN — server did not respond yet; check ~/rm2_preview.log"
fi
