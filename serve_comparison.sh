#!/usr/bin/env bash
set -euo pipefail

# Serve the reCompose comparison viewer
# Binds to 192.168.88.102:8731

HOST="192.168.88.102"
PORT="8731"
DIR="$(cd "$(dirname "$0")" && pwd)/comparison"

echo "reCompose Build Comparison Viewer"
echo "=================================="
echo "Serving: $DIR"
echo "URL:     http://$HOST:$PORT/"
echo ""
echo "Press Ctrl+C to stop"
echo ""

exec python3 -m http.server "$PORT" --bind "$HOST" --directory "$DIR"
