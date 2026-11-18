#!/usr/bin/env bash
# Installer for RedNoteScraper on a Raspberry Pi (64-bit Raspberry Pi OS / Debian).
#   curl -fsSL https://raw.githubusercontent.com/elvinzhou/RedNoteScraper/main/install.sh | bash
# Override with: REF=<branch> INSTALL_DIR=<dir>   (run as a normal user, not root)
set -euo pipefail

REPO="https://github.com/elvinzhou/RedNoteScraper.git"
REF="${REF:-main}"
DIR="${INSTALL_DIR:-$HOME/RedNoteScraper}"

[ "$(id -u)" -ne 0 ] || { echo "Run as a normal user (it uses sudo for apt)." >&2; exit 1; }
[ "$(uname -m)" = "aarch64" ] || echo "Warning: expected 64-bit ARM (aarch64), got $(uname -m)."

echo "==> Installing system packages"
sudo apt-get update
sudo apt-get install -y git python3-venv
if ! sudo apt-get install -y chromium; then
  sudo apt-get install -y chromium-browser
fi
BROWSER="$(command -v chromium || command -v chromium-browser)"

echo "==> Fetching code into $DIR"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch origin "$REF" && git -C "$DIR" checkout "$REF" && git -C "$DIR" pull --ff-only origin "$REF"
else
  git clone --branch "$REF" "$REPO" "$DIR"
fi

echo "==> Setting up Python environment"
cd "$DIR"
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s|^# REDNOTE_BROWSER_PATH=.*|REDNOTE_BROWSER_PATH=$BROWSER|" .env
  chmod 600 .env
fi

echo "==> Installing systemd user units (not started yet)"
mkdir -p "$HOME/.config/systemd/user"
cp deploy/rednote-slots.service deploy/rednote-slots.timer "$HOME/.config/systemd/user/"
sudo loginctl enable-linger "$USER" || true
systemctl --user daemon-reload || echo "(systemd user session unavailable; run 'systemctl --user daemon-reload' later)"

cat <<MSG

Installed. Remaining steps:
  1. Edit $DIR/.env  (set KEYWORDS, the term lists and a Discord/Telegram/SMTP channel)
  2. Log in to RedNote once, on the Pi's desktop or over VNC (use a throwaway account):
       cd $DIR && .venv/bin/python rednote_slots.py --login
  3. Start the schedule:
       systemctl --user enable --now rednote-slots.timer
MSG
