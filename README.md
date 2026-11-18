# RedNoteScraper

Proof-of-concept watcher for [RedNote (Xiaohongshu)](https://www.xiaohongshu.com). It searches for configurable keywords, filters note titles with regexes, and notifies Discord, Telegram and/or email about matches. The default config looks for posts about new US visa appointment slots (usvisascheduling), but anything can be watched.

> **Heads up:** automated access likely breaches RedNote's terms of service and the site blocks aggressive scraping. Use a dedicated throwaway account (never your personal one), keep volume low, and treat hits as leads, not facts.

## How it works

1. A persistent Chromium profile holds your RedNote login. You log in by hand once (`--login`); no password is stored.
2. `rednote_slots.py` searches each keyword, scrolls a few times, and reads the search API JSON the page already loads.
3. A note is a hit if its title matches at least one `TOPIC_TERMS` regex **and** one `EVENT_TERMS` regex.
4. New hits are appended to `hits.jsonl`, deduplicated through `seen.json`, and sent with title, link, author, post time, likes and cover image.
5. If the session expires, a captcha appears, or RedNote blocks the network, you get one alert per `ALERT_COOLDOWN_HOURS` (with a screenshot by email) and the run exits with code 3.

It follows the same conventions as [NYTAutoRenew](https://github.com/elvinzhou/NYTAutoRenew): Python + Playwright, system Chromium on the Pi, `.env` config, a systemd user timer, and an auto-update step before each run.

## Running on a Raspberry Pi

Needs a Pi 4/5 on 64-bit Raspberry Pi OS with a desktop session (or VNC) for the one-time login.

```bash
curl -fsSL https://raw.githubusercontent.com/elvinzhou/RedNoteScraper/main/install.sh | bash
nano ~/RedNoteScraper/.env                      # KEYWORDS, term lists, a notification channel
cd ~/RedNoteScraper
.venv/bin/python rednote_slots.py --login   # log in once in the window that opens, then press Enter
systemctl --user enable --now rednote-slots.timer
```

The installer sets up apt packages (`chromium`, `python3-venv`), a venv, `.env` (with `REDNOTE_BROWSER_PATH` pointing at system Chromium) and the systemd user units, without starting them. Re-running it updates the code and keeps `.env`.

Useful commands:

```bash
systemctl --user list-timers rednote-slots.timer
journalctl --user -u rednote-slots -n 50
systemctl --user stop rednote-slots.timer       # do this before logging in again (profile lock)
```

The timer runs 2 minutes after boot, then every 15 minutes with up to 90 s of random delay. When the alert fires, stop the timer, repeat the manual login step, and start it again.

## Local development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest          # offline tests, no browser or network needed
cp .env.example .env && .venv/bin/python rednote_slots.py
```

## Configuration

Environment variables, or a `.env` next to the script (real env vars win). See `.env.example`.

| Variable | Purpose | Default |
|---|---|---|
| `KEYWORDS` | Comma-separated search terms (required) | |
| `TOPIC_TERMS` | Comma-separated regexes; title must match one | none (skip check) |
| `EVENT_TERMS` | Comma-separated regexes; title must match one | none (skip check) |
| `DISCORD_WEBHOOK_URL` | Discord channel webhook | |
| `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram bot credentials | |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_TO` | Email | |
| `MAX_SCROLLS` | Scroll passes per keyword | `4` |
| `DELAY_MIN_MS`, `DELAY_MAX_MS` | Random pause between actions | `2500`, `5000` |
| `RETRIES`, `RETRY_DELAY` | Page-load retries, doubling delay (s) | `3`, `30` |
| `ALERT_COOLDOWN_HOURS` | Min hours between alerts | `24` |
| `DATA_DIR` | Where `hits.jsonl` / `seen.json` live | script dir |
| `REDNOTE_HEADLESS` | `0` runs the scraper visibly | `1` |
| `REDNOTE_PROFILE_DIR` | Browser profile dir | `./.browser-profile` |
| `REDNOTE_BROWSER_PATH` | System browser, e.g. `/usr/bin/chromium` | bundled |
| `REDNOTE_AUTO_UPDATE`, `REDNOTE_UPDATE_REF` | Pull `main` before each scheduled run; `0` disables | on, `main` |

Any configured channel receives every hit and every alert; one failing channel never blocks the others. Terms are case-insensitive regexes, so a comma can't appear inside one.

Discord needs only a [webhook](https://support.discord.com/hc/en-us/articles/228383668) (Channel settings, Integrations, Webhooks), not a bot. For Telegram, create a bot with @BotFather and message it once to get your chat ID.

Exit codes: `0` ok, `1` every search failed (network), `2` bad config, `3` login needed or network blocked.

## Known limitations

- **Blocked networks:** RedNote answers some IPs (datacenter, VPN, some clouds) with a "安全限制 / error 300012" page. The scraper detects it and alerts, but a new login won't help; use a home connection (such as the Pi on your LAN).
- Only note **titles** are matched; the search API doesn't return bodies. Fetching note details would allow body matching.
- API field names (`note_card.cover`, `corner_tag_info`, ...) are best guesses and the logged-out detection is heuristic. Verify against a real logged-in run and adjust `normalise_note()` / `page_problem()` in `rednote_slots.py`.
- The "sort by newest" click is best effort.
- Sessions expire eventually; re-login when the alert fires.
