#!/usr/bin/env python3
"""Watch RedNote (Xiaohongshu) for notes matching configurable keywords.

Searches each keyword, captures the search API JSON the page itself loads, keeps notes
whose title matches a TOPIC term and an EVENT term, and notifies Discord / Telegram /
email. Default config targets "new usvisascheduling slots" posts, but any topic works.

RedNote password is never stored: log in once by hand (`--login`); the session
lives in the persistent browser profile. If a later run lands on a login/captcha page it
sends one alert (per cooldown) and exits with code 3 so you can log in again.

Configuration (environment variables or a .env file next to this script):
  KEYWORDS         comma-separated search terms (required)
  TOPIC_TERMS      comma-separated regexes (case-insensitive); title must match one.
                   Empty = skip this check.
  EVENT_TERMS      same; title must also match one of these. Empty = skip.
  DISCORD_WEBHOOK_URL
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
  SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, MAIL_TO, MAIL_FROM
                   any configured channel receives every hit and every alert
  MAX_SCROLLS (4)  max scroll passes per keyword; scrolling stops early once a pass
                   brings nothing new, and is skipped if the newest results are all seen
  MAX_NOTIFY_PER_RUN (10)  hits beyond this arrive as one summary message (0 = no cap)
  DELAY_MIN_MS (2500), DELAY_MAX_MS (5000)  random pause between actions
  RETRIES (3), RETRY_DELAY (30)  page-load errors retry with doubling delay
  ALERT_COOLDOWN_HOURS (24)  min hours between "session expired" alerts
  DATA_DIR         where hits.jsonl / seen.json live (default: next to this script)
  REDNOTE_HEADLESS "0" to run visibly (a login prompt then waits for you; default "1")
  REDNOTE_PROFILE_DIR   browser profile dir (default ./.browser-profile)
  REDNOTE_BROWSER_PATH  path to a system browser, e.g. /usr/bin/chromium (optional)
  REDNOTE_BASE_URL  site to use, e.g. https://www.rednote.com. Default: whatever
                   www.xiaohongshu.com redirects you to (logins are per domain)

Run `python rednote_slots.py --login` once to log in by hand (opens a window, then
verifies with a test search). Exit codes: 0 ok, 1 every search failed (network?), 2 bad config, 3 login/block (alerted).
"""
import json
import os
import random
import re
import smtplib
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
SHOTS = HERE / "screenshots"
MANUAL_LOGIN_WAIT = 600  # seconds to wait for a human in headful mode
SEARCH_API = "/api/sns/web/v1/search/notes"
DEFAULT_BASE = "https://www.xiaohongshu.com"  # may redirect to www.rednote.com by region
USER_AGENT = "rednote-slots/0.1"  # Discord's edge rejects the default urllib UA


class SessionProblem(Exception):
    """Login needed, captcha, or the network/IP is blocked."""

    def __init__(self, msg: str, attachment: Path | None = None):
        super().__init__(msg)
        self.attachment = attachment


# ----------------------------------------------------------------------------- config
def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def split_list(value: str | None) -> list[str]:
    return [s.strip() for s in (value or "").split(",") if s.strip()]


def compile_terms(name: str, value: str | None) -> list[re.Pattern]:
    out = []
    for term in split_list(value):
        try:
            out.append(re.compile(term, re.I))
        except re.error as e:
            raise ValueError(f"{name}: bad regex {term!r}: {e}") from e
    return out


@dataclass
class Config:
    keywords: list[str]
    topic: list[re.Pattern]
    event: list[re.Pattern]
    max_scrolls: int
    delay: tuple[float, float]  # seconds
    retries: int
    retry_delay: int
    alert_cooldown_hours: float
    data_dir: Path
    headless: bool
    profile_dir: Path
    browser_path: str | None
    base_url: str | None = None
    max_notify: int = 10

    @classmethod
    def from_env(cls, env=None) -> "Config":
        env = os.environ if env is None else env
        data_dir = Path(env.get("DATA_DIR") or HERE)
        return cls(
            keywords=split_list(env.get("KEYWORDS")),
            topic=compile_terms("TOPIC_TERMS", env.get("TOPIC_TERMS")),
            event=compile_terms("EVENT_TERMS", env.get("EVENT_TERMS")),
            max_scrolls=int(env.get("MAX_SCROLLS") or 4),
            delay=(int(env.get("DELAY_MIN_MS") or 2500) / 1000,
                   int(env.get("DELAY_MAX_MS") or 5000) / 1000),
            retries=max(1, int(env.get("RETRIES") or 3)),
            retry_delay=int(env.get("RETRY_DELAY") or 30),
            alert_cooldown_hours=float(env.get("ALERT_COOLDOWN_HOURS") or 24),
            data_dir=data_dir,
            headless=env.get("REDNOTE_HEADLESS", "1") != "0",
            profile_dir=Path(env.get("REDNOTE_PROFILE_DIR") or HERE / ".browser-profile"),
            browser_path=env.get("REDNOTE_BROWSER_PATH") or None,
            base_url=(env.get("REDNOTE_BASE_URL") or "").rstrip("/") or None,
            max_notify=int(env.get("MAX_NOTIFY_PER_RUN") or 10),
        )

    @property
    def hits_file(self) -> Path:
        return self.data_dir / "hits.jsonl"

    @property
    def seen_file(self) -> Path:
        return self.data_dir / "seen.json"

    @property
    def alert_file(self) -> Path:
        return self.data_dir / ".session_alert"


# ----------------------------------------------------------------------------- parsing
def is_match(text: str, topic: list[re.Pattern], event: list[re.Pattern]) -> bool:
    t = text or ""
    return ((not topic or any(r.search(t) for r in topic))
            and (not event or any(r.search(t) for r in event)))


def normalise_note(item: dict, base: str = DEFAULT_BASE) -> dict | None:
    """One item of RedNote's search response -> flat dict (field names are best guesses)."""
    card = item.get("note_card") or {}
    note_id = item.get("id") or card.get("note_id")
    if not note_id:
        return None
    user = card.get("user") or {}
    cover = card.get("cover") or {}
    tags = card.get("corner_tag_info") or []
    url = f"{base}/explore/{note_id}"
    if item.get("xsec_token"):
        url += f"?xsec_token={quote(item['xsec_token'], safe='')}&xsec_source=pc_search"
    return {
        "id": note_id,
        "title": card.get("display_title") or "",
        "user": user.get("nickname") or user.get("nick_name") or "",
        "likes": (card.get("interact_info") or {}).get("liked_count") or "",
        "type": card.get("type") or "",
        "cover": cover.get("url_default") or cover.get("url_pre") or cover.get("url") or "",
        "posted": next((t.get("text", "") for t in tags if t.get("type") == "publish_time"), ""),
        "url": url,
    }


def extract_notes(payload: dict, base: str = DEFAULT_BASE) -> list[dict]:
    items = ((payload or {}).get("data") or {}).get("items") or []
    return [n for n in (normalise_note(i, base) for i in items) if n]


# ----------------------------------------------------------------------------- notifications
def post_json(url: str, body: dict, timeout: int = 20) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"content-type": "application/json", "user-agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)


def hit_summary(hit: dict) -> str:
    meta = " · ".join(x for x in (hit.get("user"), hit.get("posted"),
                                  hit.get("likes") and f"♥ {hit['likes']}") if x)
    return "\n".join(x for x in (hit.get("title"), meta, hit.get("url")) if x)


def discord_payload(hit: dict | None = None, text: str | None = None) -> dict:
    if text is not None:
        return {"username": "RedNote slots", "content": text[:2000]}
    meta = "  ·  ".join(x for x in (hit.get("user") and f"👤 {hit['user']}",
                                    hit.get("posted") and f"🕒 {hit['posted']}",
                                    hit.get("likes") and f"❤️ {hit['likes']}",
                                    hit.get("keyword") and f"🔎 {hit['keyword']}") if x)
    embed = {"title": (hit.get("title") or "(no title)")[:256], "url": hit["url"],
             "description": meta}
    if hit.get("cover"):
        embed["image"] = {"url": hit["cover"]}
    if hit.get("found_at"):
        embed["timestamp"] = hit["found_at"]
    return {"username": "RedNote slots", "embeds": [embed]}


def send_discord(hit=None, text=None) -> bool:
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        return False
    body = discord_payload(hit, text)
    status, headers = post_json(url, body)
    if status == 429:  # rate limited: wait as told, retry once
        time.sleep(min(float(headers.get("Retry-After", headers.get("retry-after", 1))), 30))
        status, _ = post_json(url, body)
    if not 200 <= status < 300:
        raise RuntimeError(f"Discord returned {status}")
    return True


def send_telegram(hit=None, text=None) -> bool:
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    api = f"https://api.telegram.org/bot{token}/"
    caption = text if text is not None else hit_summary(hit)
    status = 0
    if hit and hit.get("cover"):
        status, _ = post_json(api + "sendPhoto",
                              {"chat_id": chat, "photo": hit["cover"], "caption": caption[:1024]})
    if not 200 <= status < 300:  # no cover, or Telegram couldn't fetch it
        status, _ = post_json(api + "sendMessage", {"chat_id": chat, "text": caption})
    if not 200 <= status < 300:
        raise RuntimeError(f"Telegram returned {status}")
    return True


def send_email(subject: str, body: str, attachment: Path | None = None) -> bool:
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER", "")
    to = os.environ.get("MAIL_TO", user)
    if not host or not to:
        return False
    msg = EmailMessage()
    msg["Subject"], msg["To"] = subject, to
    msg["From"] = os.environ.get("MAIL_FROM", user or to)
    msg.set_content(body)
    if attachment and attachment.exists():
        msg.add_attachment(attachment.read_bytes(), maintype="image",
                           subtype="png", filename=attachment.name)
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
        s.starttls()
        if user:
            s.login(user, os.environ.get("SMTP_PASSWORD", ""))
        s.send_message(msg)
    return True


def _deliver(jobs: dict) -> bool:
    """Run every configured channel; one failing never blocks the others."""
    delivered = False
    for name, fn in jobs.items():
        try:
            if fn():
                delivered = True
        except Exception as e:  # noqa: BLE001
            log(f"{name} notification failed: {e!r}")
    return delivered


def notify_hit(hit: dict) -> bool:
    return _deliver({
        "discord": lambda: send_discord(hit=hit),
        "telegram": lambda: send_telegram(hit=hit),
        "email": lambda: send_email(f"RedNote: {hit.get('title') or hit['id']}"[:150],
                                    hit_summary(hit)),
    })


def notify_text(subject: str, text: str, attachment: Path | None = None) -> bool:
    return _deliver({
        "discord": lambda: send_discord(text=text),
        "telegram": lambda: send_telegram(text=text),
        "email": lambda: send_email(subject, text, attachment),
    })


# ----------------------------------------------------------------------------- session alerts
def alert_due(alert_file: Path, cooldown_hours: float, now: float | None = None) -> bool:
    try:
        last = float(alert_file.read_text())
    except (OSError, ValueError):
        return True
    return (now or time.time()) - last > cooldown_hours * 3600


def session_problem(cfg: Config, reason: str, attachment: Path | None = None) -> None:
    log(f"Session problem: {reason}")
    if not alert_due(cfg.alert_file, cfg.alert_cooldown_hours):
        log("Alert suppressed (cooldown)")
        return
    text = (f"⚠️ RedNote scraper stopped: {reason}\n"
            "If it's a login: stop the timer, run `.venv/bin/python "
            "rednote_slots.py --login` on the Pi's desktop/VNC, then start the timer. "
            "If the network is blocked, log in won't help: change IP / wait.")
    if notify_text("RedNote scraper needs attention", text, attachment):
        cfg.alert_file.parent.mkdir(parents=True, exist_ok=True)
        cfg.alert_file.write_text(str(time.time()))


# ----------------------------------------------------------------------------- browser
def shot(page, name: str) -> Path:
    SHOTS.mkdir(exist_ok=True)
    path = SHOTS / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)
    return path


def visible(locator, timeout: int = 3000) -> bool:
    try:
        locator.first.wait_for(state="visible", timeout=timeout)
        return True
    except Exception:  # noqa: BLE001
        return False


def page_problem(page) -> tuple[str, str] | None:
    """Detect what's wrong with the current page: ("block"|"login", reason), or None.

    Best-effort heuristics: a redirect to /website-login/error with "安全限制" (security
    restriction, code 300012) means RedNote blocked this IP; other login/captcha URLs or a
    visible "登录" (log in) button mean the session needs a login.
    """
    if "website-login/error" in page.url or visible(page.get_by_text("安全限制"), 500):
        return ("block", "RedNote blocked this network (安全限制 / error 300012). "
                         "Datacenter and VPN IPs are often flagged; try another connection")
    if re.search(r"login|captcha|verify", page.url, re.I):
        return ("login", "RedNote is showing a login/captcha page (session expired or blocked)")
    try:  # logged-out search box reads "登录探索更多内容" ("log in to explore more")
        placeholder = page.get_attribute("#search-input", "placeholder", timeout=500) or ""
    except Exception:  # noqa: BLE001
        placeholder = ""
    if re.search(r"登录|log ?in|sign ?in", placeholder, re.I) or any(
            visible(page.get_by_text(t, exact=True), 300) for t in ("登录", "Log in", "Log In", "Sign in")):
        return ("login", "RedNote is asking for a login (session expired)")
    return None


def wait_for_results(page, state: dict, timeout: float = 15) -> tuple[str, str] | None:
    """Wait for the search API to answer; return a problem if the page goes wrong first."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if state["searches"]:
            return None
        problem = page_problem(page)
        if problem:
            return problem
        page.wait_for_timeout(500)
    return ("login", "No search results loaded (session expired, or blocked silently)")


def wait_for_human(message: str) -> None:
    """Block until the person says they're done (Enter), or time out without a terminal."""
    if sys.stdin.isatty():
        input(f"\n{message}\nPress Enter here when you're done... ")
    else:
        log(f"{message} (no terminal for Enter; waiting {MANUAL_LOGIN_WAIT // 60} min)")
        time.sleep(MANUAL_LOGIN_WAIT)


def handle_problem(page, cfg: Config, problem: tuple[str, str]) -> None:
    """Raise SessionProblem, except in headful mode where a human can fix a login.

    The page heuristics can't reliably tell when someone has finished logging in, so the
    caller is asked (Enter) and then re-checks by actually running the search again.
    """
    kind, reason = problem
    if cfg.headless or kind == "block":
        raise SessionProblem(reason, shot(page, f"{kind}-problem"))
    log(reason)
    goto_with_retry(page, (cfg.base_url or origin_of(page.url)) + "/explore", cfg)
    wait_for_human("Log in to RedNote in the browser window (throwaway account).")


def goto_with_retry(page, url: str, cfg: Config) -> None:
    delay = cfg.retry_delay
    for attempt in range(1, cfg.retries + 1):
        try:
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            return
        except Exception as e:  # noqa: BLE001
            if attempt == cfg.retries:
                raise
            log(f"Load failed ({e!r}); retry {attempt}/{cfg.retries - 1} in {delay}s")
            time.sleep(delay)
            delay *= 2


def pause(page, cfg: Config) -> None:
    # page.wait_for_timeout (not time.sleep) so response handlers keep running while we wait
    page.wait_for_timeout(random.uniform(*cfg.delay) * 1000)


def load_seen(cfg: Config) -> set[str]:
    try:
        return set(json.loads(cfg.seen_file.read_text()))
    except (OSError, ValueError):
        return set()


def origin_of(url: str) -> str:
    m = re.match(r"(https?://[^/]+)", url or "")
    return m.group(1) if m else DEFAULT_BASE


def search_url(kw: str, base: str = DEFAULT_BASE) -> str:
    return f"{base}/search_result?keyword={quote(kw)}&source=web_search_result_notes"


def open_context(p, cfg: Config):
    kwargs = dict(headless=cfg.headless, locale="zh-CN", viewport={"width": 1280, "height": 900})
    if cfg.browser_path:
        kwargs["executable_path"] = cfg.browser_path
    return p.chromium.launch_persistent_context(str(cfg.profile_dir), **kwargs)


def attach_listeners(page, state: dict, on_payload=None) -> None:
    """Count search API answers, remember recent API calls for diagnostics, hand JSON on."""
    calls = state.setdefault("calls", [])

    def on_response(res):
        url = res.url
        if "/api/" in url and re.search(r"xiaohongshu|rednote", url):
            calls.append(f"{res.status} {url.split('?')[0][-70:]}")
            del calls[:-15]
        if SEARCH_API not in url:
            return
        state["searches"] += 1
        if on_payload:
            try:
                on_payload(res.json())
            except Exception:  # noqa: BLE001  (non-JSON / aborted)
                pass

    page.on("response", on_response)


def describe_page(page, state: dict, context=None) -> str:
    """One-paragraph snapshot for debugging a failed check (never includes cookie values)."""
    try:
        placeholder = page.get_attribute("#search-input", "placeholder", timeout=500)
    except Exception:  # noqa: BLE001
        placeholder = None
    try:
        names = sorted({c["name"] for c in context.cookies()}) if context else []
    except Exception:  # noqa: BLE001
        names = []
    try:
        title = page.title()
    except Exception:  # noqa: BLE001
        title = None
    return (f"url={page.url} title={title!r} search_box_placeholder={placeholder!r} "
            f"search_answers={state.get('searches', 0)} has_web_session_cookie={'web_session' in names} "
            f"recent_api_calls={state.get('calls', [])[-8:]}")


def resolve_base(page, cfg: Config) -> str:
    """Logins are per domain, and xiaohongshu.com may redirect to rednote.com: follow it."""
    if cfg.base_url:
        return cfg.base_url
    goto_with_retry(page, DEFAULT_BASE + "/explore", cfg)
    return origin_of(page.url)


def login_flow(cfg: Config) -> int:
    """`--login`: open the home page, let a human log in, then prove it with a real search."""
    from playwright.sync_api import sync_playwright

    cfg.headless = False
    with sync_playwright() as p:
        context = open_context(p, cfg)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            state = {"searches": 0}
            attach_listeners(page, state)
            base = resolve_base(page, cfg)
            log(f"Using site {base}")
            wait_for_human("Log in to RedNote in the browser window (use a throwaway account; "
                           "click the login button, finish any captcha/2FA).")
            base = cfg.base_url or origin_of(page.url)  # you may have been sent elsewhere
            log(f"Checking the login with a test search on {base} ...")
            state["searches"] = 0
            goto_with_retry(page, search_url(cfg.keywords[0] if cfg.keywords else "usvisascheduling", base), cfg)
            problem = wait_for_results(page, state, timeout=30)
            if problem:
                log(f"Not logged in yet: {problem[1]}")
                log(f"Details: {describe_page(page, state, context)}")
                log(f"Screenshot: {shot(page, 'login-check')}")
                if sys.stdin.isatty():
                    input("The window stays open so you can look at it. Press Enter to close... ")
                return 3
            log(f"Login OK: the search returned results on {base}. The session is saved in "
                f"{cfg.profile_dir}. You can start the timer now.")
            return 0
        finally:
            context.close()


def scroll_for_new(page, batch: list[dict], seen: set[str], cfg: Config, newest_first: bool) -> None:
    """Scroll for more results, but only as far as there is something new to find."""
    def unseen() -> int:
        return sum(1 for n in batch if n["id"] not in seen)

    if newest_first and not unseen():
        log("  nothing new in the newest results; not scrolling")
        return
    for _ in range(cfg.max_scrolls):
        total, fresh = len(batch), unseen()
        page.mouse.wheel(0, 2500)
        pause(page, cfg)
        if len(batch) == total:
            break  # no more results loaded
        if newest_first and unseen() == fresh:
            break  # reached posts we've already seen


class Notifier:
    """Sends hits, but caps a single run so a flood becomes one summary message."""

    def __init__(self, cap: int):
        self.cap, self.sent, self.held = cap, 0, []

    def hit(self, hit: dict) -> None:
        if self.cap and self.sent >= self.cap:
            self.held.append(hit)
            return
        self.sent += 1
        notify_hit(hit)

    def flush(self) -> None:
        if not self.held:
            return
        lines = [f"RedNote: {len(self.held)} more matching post(s) (cap {self.cap} per run; "
                 "all are in hits.jsonl):"]
        lines += [f"• {h.get('title') or h['id']} {h['url']}" for h in self.held[:10]]
        if len(self.held) > 10:
            lines.append(f"…and {len(self.held) - 10} more")
        notify_text("RedNote: more matches", "\n".join(lines))
        self.held = []


def run(cfg: Config) -> int:
    from playwright.sync_api import sync_playwright  # lazy: tests don't need it

    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    seen = load_seen(cfg)
    new_hits = failures = 0
    notifier = Notifier(cfg.max_notify)

    with sync_playwright() as p:
        context = open_context(p, cfg)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            batch: list[dict] = []
            state = {"searches": 0, "base": DEFAULT_BASE}
            attach_listeners(page, state, lambda payload: batch.extend(extract_notes(payload, state["base"])))
            try:
                state["base"] = resolve_base(page, cfg)
            except Exception as e:  # noqa: BLE001
                log(f"Could not load the home page: {e!r}")
                return 1

            for kw in cfg.keywords:
                batch.clear()
                state["searches"] = 0
                log(f"Searching: {kw}")
                url = search_url(kw, state["base"])
                try:
                    goto_with_retry(page, url, cfg)
                except Exception as e:  # noqa: BLE001
                    log(f"Giving up on {kw!r}: {e!r}")
                    failures += 1
                    continue
                problem = wait_for_results(page, state)
                if problem:
                    log(f"Problem: {describe_page(page, state, context)}")
                    handle_problem(page, cfg, problem)  # raises unless a human can fix it
                    state["searches"] = 0
                    state["base"] = cfg.base_url or origin_of(page.url)
                    url = search_url(kw, state["base"])
                    goto_with_retry(page, url, cfg)
                    problem = wait_for_results(page, state)
                    if problem:
                        log(f"Still failing: {describe_page(page, state, context)}")
                        raise SessionProblem(problem[1], shot(page, "problem"))
                pause(page, cfg)
                newest_first = False
                for label in ("最新", "Latest"):  # best effort: sort by newest
                    try:
                        page.get_by_text(label, exact=True).first.click(timeout=2000)
                        newest_first = True
                        break
                    except Exception:  # noqa: BLE001
                        pass
                pause(page, cfg)
                scroll_for_new(page, batch, seen, cfg, newest_first)

                for note in list(batch):
                    if note["id"] in seen:
                        continue
                    seen.add(note["id"])
                    if not is_match(note["title"], cfg.topic, cfg.event):
                        continue
                    hit = {**note, "keyword": kw,
                           "found_at": datetime.now(timezone.utc).isoformat()}
                    with cfg.hits_file.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(hit, ensure_ascii=False) + "\n")
                    log(f"HIT: {note['title']}  {note['url']}")
                    new_hits += 1
                    notifier.hit(hit)
                cfg.seen_file.write_text(json.dumps(sorted(seen)))
        finally:
            notifier.flush()
            context.close()

    if failures == len(cfg.keywords):
        log("Every search failed; check the network")
        return 1
    cfg.alert_file.unlink(missing_ok=True)  # healthy run: re-arm the alert
    log(f"Done. {new_hits} new hit(s) -> {cfg.hits_file}")
    return 0


def main() -> int:
    load_dotenv(HERE / ".env")
    try:
        cfg = Config.from_env()
    except ValueError as e:
        log(f"Config error: {e}")
        return 2
    if "--login" in sys.argv[1:]:
        return login_flow(cfg)
    if not cfg.keywords:
        log("KEYWORDS is empty. Copy .env.example to .env and set it.")
        return 2
    try:
        return run(cfg)
    except SessionProblem as e:
        session_problem(cfg, str(e), e.attachment)
        return 3


if __name__ == "__main__":
    sys.exit(main())
