#!/usr/bin/env bash
# Install the web app as a systemd user service that starts at boot and restarts on failure.
#   scripts/install_service.sh [--port 8000] [--https-port 8444]
# --https-port also publishes it on your tailnet over HTTPS with `tailscale serve` (tailnet only).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT=8000
HTTPS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2;;
    --https-port) HTTPS="$2"; shift 2;;
    *) echo "unknown option $1" >&2; exit 2;;
  esac
done
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
sed -e "s#@ROOT@#$ROOT#g" -e "s#@PORT@#$PORT#g" "$ROOT/deploy/kumiko-mosaic.service" > "$UNIT_DIR/kumiko-mosaic.service"
systemctl --user daemon-reload
systemctl --user enable --now kumiko-mosaic.service
loginctl enable-linger "$USER" 2>/dev/null || true      # keep the user manager (and this service) running after logout
sleep 2
systemctl --user --no-pager --lines=3 status kumiko-mosaic.service | head -8
if [ -n "$HTTPS" ]; then
  tailscale serve --bg --https="$HTTPS" "http://127.0.0.1:$PORT"
  echo "HTTPS (tailnet only): https://$(tailscale status --json | python3 -c 'import sys,json;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'):$HTTPS"
fi
echo "Logs: journalctl --user -u kumiko-mosaic -f     Stop: systemctl --user disable --now kumiko-mosaic"
