# RedNoteScraper

Proof-of-concept watcher for [RedNote (Xiaohongshu)](https://www.xiaohongshu.com). It searches for configurable keywords, filters note titles with regexes, and sends matches to Discord and/or Telegram. The default config looks for mentions of new US visa appointment slots (usvisascheduling), but anything can be watched.

> **Heads up:** automated access likely breaches RedNote's terms of service and the site blocks aggressive scraping. Use a dedicated throwaway account (never your personal one), keep volume low, and treat hits as leads, not facts.

## How it works

1. `login.js` opens a visible browser; you log in manually and the session is saved to `auth.json`.
2. `scrape.js` loads the saved session headlessly, searches each keyword, scrolls a few times, and reads the search API JSON the page already loads.
3. A note is a hit if its title matches at least one `TOPIC_TERMS` regex **and** one `EVENT_TERMS` regex.
4. New hits are appended to `hits.jsonl`, deduplicated through `seen.json`, and posted to your notification channel with title, link, author, post time, likes and cover image.
5. If the session expires or a login/captcha page appears, you get a plain-text alert (at most once per `ALERT_COOLDOWN_HOURS`) and the run exits with code 3.

## Setup

Requires Node 18+.

```bash
npm install
npx playwright install --with-deps chromium
cp .env.example .env     # set KEYWORDS, term lists and a webhook
npm run login            # on a machine with a display
npm run scrape           # add HEADED=1 to watch the browser
npm test
```

## Configuration

All settings are environment variables, or a `.env` file in the working directory (real env vars win).

| Variable | Purpose | Default |
|---|---|---|
| `KEYWORDS` | Comma-separated search terms (required) | |
| `TOPIC_TERMS` | Comma-separated regexes; title must match one | none (skip check) |
| `EVENT_TERMS` | Comma-separated regexes; title must match one | none (skip check) |
| `DISCORD_WEBHOOK_URL` | Discord channel webhook | |
| `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram bot credentials | |
| `MAX_SCROLLS` | Scroll passes per keyword | `4` |
| `DELAY_MIN_MS`, `DELAY_MAX_MS` | Random pause between actions | `2500`, `5000` |
| `ALERT_COOLDOWN_HOURS` | Min hours between session-expired alerts | `24` |
| `DATA_DIR` | Where `auth.json`, `seen.json`, `hits.jsonl` live | `.` |

Terms are case-insensitive regexes, so a comma can't appear inside one. Set either or both notification channels.

Discord needs only a [webhook](https://support.discord.com/hc/en-us/articles/228383668) (Channel settings, Integrations, Webhooks), not a bot. For Telegram, create a bot with @BotFather and message it once to get your chat ID.

## Running on a Raspberry Pi

Use a Pi 4 or 5 (4 GB+) with 64-bit Raspberry Pi OS. Do the login on your laptop and copy `auth.json` to the Pi. If the bundled Chromium misbehaves, `apt install chromium` and launch with `executablePath: '/usr/bin/chromium'`.

```bash
deploy/install.sh          # or: deploy/install.sh 10min
systemctl list-timers rednote-slots.timer
journalctl -u rednote-slots -n 50
```

This installs a oneshot systemd service and a timer (first run 2 minutes after boot, then every 15 minutes with up to 90 s of random delay). `.env` and `auth.json` must exist first.

## Known limitations

- Only note **titles** are matched; the search API doesn't return bodies. Fetching note details would allow body matching.
- API field names (`note_card.cover`, `corner_tag_info`, ...) and the logged-out detection in `scrape.js` are best guesses. Verify against a real response and adjust `filter.js` / `looksLoggedOut()`.
- The "sort by newest" click is best effort.
- Sessions expire eventually; re-run `npm run login` and copy `auth.json` over when the alert fires.
