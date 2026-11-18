#!/usr/bin/env bash
# Installs the systemd service + timer for this checkout. Usage: deploy/install.sh [interval, default 15min]
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
INTERVAL="${1:-15min}"
NODE="$(command -v node)"
for f in .env auth.json; do [ -f "$DIR/$f" ] || { echo "Missing $DIR/$f - create it first." >&2; exit 1; }; done
render() { sed -e "s|@USER@|${SUDO_USER:-$USER}|" -e "s|@DIR@|$DIR|" -e "s|@NODE@|$NODE|" -e "s|@INTERVAL@|$INTERVAL|" "$1"; }
render "$DIR/deploy/rednote-slots.service.in" | sudo tee /etc/systemd/system/rednote-slots.service >/dev/null
render "$DIR/deploy/rednote-slots.timer.in"   | sudo tee /etc/systemd/system/rednote-slots.timer   >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now rednote-slots.timer
echo "Installed. Check: systemctl list-timers rednote-slots.timer ; journalctl -u rednote-slots -n 50"
