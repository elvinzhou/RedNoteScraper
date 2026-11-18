#!/usr/bin/env bash
# Fast-forward the install to origin/main before each scheduled run (ExecStartPre).
# Never fails the run: any problem is logged and the current code is used as-is.
# Disable with REDNOTE_AUTO_UPDATE=0 in .env. Override the branch with REDNOTE_UPDATE_REF.
cd "$(dirname "$0")/.." || exit 0

setting() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "\"'"; }
[ "${REDNOTE_AUTO_UPDATE:-$(setting REDNOTE_AUTO_UPDATE)}" = "0" ] && { echo "update: disabled"; exit 0; }
REF="${REDNOTE_UPDATE_REF:-$(setting REDNOTE_UPDATE_REF)}"; REF="${REF:-main}"

[ -d .git ] || { echo "update: not a git checkout; skipping"; exit 0; }
timeout 60 git fetch --quiet origin "$REF" || { echo "update: fetch failed (offline?); using current code"; exit 0; }

old="$(git rev-parse HEAD)"; new="$(git rev-parse FETCH_HEAD)"
[ "$old" = "$new" ] && { echo "update: already up to date"; exit 0; }
if git merge-base --is-ancestor "$new" "$old"; then echo "update: local is ahead of origin/$REF; skipping"; exit 0; fi
git merge --ff-only --quiet "$new" \
  || { echo "update: cannot fast-forward (local changes or divergence); skipping"; exit 0; }
echo "update: ${old:0:7} -> ${new:0:7}"

if ! git diff --quiet "$old" "$new" -- requirements.txt; then
  .venv/bin/pip install --quiet -r requirements.txt || echo "update: pip install failed"
fi
if ! git diff --quiet "$old" "$new" -- deploy/rednote-slots.service deploy/rednote-slots.timer; then
  cp deploy/rednote-slots.service deploy/rednote-slots.timer "$HOME/.config/systemd/user/" \
    && systemctl --user daemon-reload && echo "update: systemd units refreshed"
fi
exit 0
