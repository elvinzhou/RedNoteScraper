import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import rednote_slots as rs  # noqa: E402

ENV = {
    "KEYWORDS": "a, b ,c",
    "TOPIC_TERMS": "usvisascheduling,美签",
    "EVENT_TERMS": "放号,new slots?",
}


@pytest.fixture
def cfg(tmp_path):
    return rs.Config.from_env({**ENV, "DATA_DIR": str(tmp_path)})


@pytest.fixture
def clean_env(monkeypatch):
    for k in ("DISCORD_WEBHOOK_URL", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "SMTP_HOST", "MAIL_TO", "SMTP_USER"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_config_parsing(cfg):
    assert cfg.keywords == ["a", "b", "c"]
    assert cfg.headless is True and cfg.max_scrolls == 4 and cfg.delay == (2.5, 5.0)
    assert rs.Config.from_env({"REDNOTE_HEADLESS": "0"}).headless is False


def test_bad_regex_is_reported():
    with pytest.raises(ValueError, match="TOPIC_TERMS"):
        rs.Config.from_env({"TOPIC_TERMS": "(unclosed"})


def test_is_match(cfg):
    m = lambda t: rs.is_match(t, cfg.topic, cfg.event)  # noqa: E731
    assert m("今天usvisascheduling刷到放号了")
    assert m("NEW SLOTS for 美签")
    assert not m("美签材料清单")
    assert not m("今天吃什么")
    assert rs.is_match("anything", [], [])  # empty lists = no filtering


def test_normalise_and_extract():
    payload = {"data": {"items": [
        {"id": "n1", "xsec_token": "t/k", "note_card": {
            "display_title": "放号", "user": {"nickname": "u"},
            "interact_info": {"liked_count": "12"},
            "cover": {"url_default": "https://img/c.jpg"},
            "corner_tag_info": [{"type": "publish_time", "text": "2天前"}]}},
        {"note_card": {}},  # no id: dropped
    ]}}
    notes = rs.extract_notes(payload)
    assert len(notes) == 1
    n = notes[0]
    assert (n["user"], n["likes"], n["cover"], n["posted"]) == ("u", "12", "https://img/c.jpg", "2天前")
    assert "xsec_token=t%2Fk" in n["url"]
    assert rs.extract_notes({}) == [] and rs.extract_notes(None) == []


def test_discord_payload():
    hit = {"title": "t", "url": "https://x/y", "user": "u", "cover": "https://img/c.jpg",
           "keyword": "k", "found_at": "2026-01-01T00:00:00+00:00"}
    e = rs.discord_payload(hit)["embeds"][0]
    assert e["image"]["url"] == "https://img/c.jpg" and e["url"] == "https://x/y"
    assert rs.discord_payload(text="hi")["content"] == "hi"


def test_notify_hit_fans_out(clean_env):
    calls = []
    clean_env.setattr(rs, "post_json", lambda url, body, timeout=20: calls.append((url, body)) or (200, {}))
    clean_env.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/hook")
    clean_env.setenv("TELEGRAM_TOKEN", "T")
    clean_env.setenv("TELEGRAM_CHAT_ID", "1")
    hit = {"id": "n1", "title": "t", "url": "https://x/y", "cover": "https://img/c.jpg"}
    assert rs.notify_hit(hit) is True
    urls = [u for u, _ in calls]
    assert "https://discord.test/hook" in urls and any(u.endswith("/sendPhoto") for u in urls)


def test_telegram_falls_back_to_text_when_photo_fails(clean_env):
    seen = []
    def fake(url, body, timeout=20):
        seen.append(url.rsplit("/", 1)[-1])
        return (400, {}) if url.endswith("sendPhoto") else (200, {})
    clean_env.setattr(rs, "post_json", fake)
    clean_env.setenv("TELEGRAM_TOKEN", "T")
    clean_env.setenv("TELEGRAM_CHAT_ID", "1")
    assert rs.send_telegram(hit={"title": "t", "url": "u", "cover": "c"}) is True
    assert seen == ["sendPhoto", "sendMessage"]


def test_discord_retries_once_on_429(clean_env):
    statuses = iter([(429, {"Retry-After": "0"}), (204, {})])
    clean_env.setattr(rs, "post_json", lambda *a, **k: next(statuses))
    clean_env.setattr(rs.time, "sleep", lambda s: None)
    clean_env.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/hook")
    assert rs.send_discord(text="x") is True


def test_one_failing_channel_does_not_block_others(clean_env):
    def fake(url, body, timeout=20):
        return (500, {}) if "discord" in url else (200, {})
    clean_env.setattr(rs, "post_json", fake)
    clean_env.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/hook")
    clean_env.setenv("TELEGRAM_TOKEN", "T")
    clean_env.setenv("TELEGRAM_CHAT_ID", "1")
    assert rs.notify_text("s", "body") is True


def test_nothing_configured_means_not_delivered(clean_env):
    assert rs.notify_text("s", "body") is False


def test_alert_cooldown(tmp_path):
    f = tmp_path / ".session_alert"
    assert rs.alert_due(f, 24)
    f.write_text("1000")
    assert not rs.alert_due(f, 24, now=1000 + 3600)
    assert rs.alert_due(f, 24, now=1000 + 25 * 3600)


def test_session_problem_alerts_once(cfg, clean_env):
    sent = []
    clean_env.setattr(rs, "notify_text", lambda subject, text, attachment=None: sent.append(text) or True)
    rs.session_problem(cfg, "expired")
    rs.session_problem(cfg, "expired")  # inside cooldown
    assert len(sent) == 1 and cfg.alert_file.exists()


def test_session_problem_retries_alert_if_undeliverable(cfg, clean_env):
    clean_env.setattr(rs, "notify_text", lambda *a, **k: False)
    rs.session_problem(cfg, "expired")
    assert not cfg.alert_file.exists()  # nothing delivered, so don't start the cooldown


def test_load_dotenv_does_not_override(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("FOO_T=from_file\nBAR_T='quoted'\n# c=1\nTERMS_T=\\bF-?1\\b\n")
    monkeypatch.setenv("FOO_T", "from_env")
    monkeypatch.delenv("BAR_T", raising=False)
    monkeypatch.delenv("TERMS_T", raising=False)
    rs.load_dotenv(env)
    assert rs.os.environ["FOO_T"] == "from_env"
    assert rs.os.environ["BAR_T"] == "quoted"
    assert rs.os.environ["TERMS_T"] == r"\bF-?1\b"


def test_empty_keywords_exits_2(monkeypatch, tmp_path):
    monkeypatch.setenv("KEYWORDS", "")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(rs, "load_dotenv", lambda p: None)
    assert rs.main() == 2


class FakeLocator:
    def __init__(self, shown): self.shown = shown
    @property
    def first(self): return self
    def wait_for(self, state, timeout):
        if not self.shown:
            raise TimeoutError()


class FakePage:
    def __init__(self, url="https://www.xiaohongshu.com/search_result", texts=(), placeholder=None):
        self.url, self.texts, self.waited, self.placeholder = url, set(texts), 0, placeholder
    def get_by_text(self, text, exact=False): return FakeLocator(text in self.texts)
    def get_attribute(self, sel, name, timeout=None): return self.placeholder
    def wait_for_timeout(self, ms): self.waited += ms
    def title(self): return "t"


def test_page_problem_detection():
    assert rs.page_problem(FakePage()) is None
    kind, _ = rs.page_problem(FakePage(url="https://www.xiaohongshu.com/website-login/error"))
    assert kind == "block"  # even though the URL also contains "login"
    assert rs.page_problem(FakePage(texts=["安全限制"]))[0] == "block"
    assert rs.page_problem(FakePage(url="https://x/captcha/verify"))[0] == "login"
    assert rs.page_problem(FakePage(texts=["登录"]))[0] == "login"


def test_wait_for_results(clean_env):
    clean_env.setattr(rs.time, "time", iter([0, 1, 2, 3, 4, 5, 6]).__next__)
    assert rs.wait_for_results(FakePage(), {"searches": 1}) is None          # API answered
    problem = rs.wait_for_results(FakePage(texts=["登录"]), {"searches": 0})  # problem first
    assert problem[0] == "login"


def test_wait_for_results_times_out_as_silent_block(clean_env):
    ticks = iter(range(0, 1000))
    clean_env.setattr(rs.time, "time", lambda: next(ticks))
    problem = rs.wait_for_results(FakePage(), {"searches": 0}, timeout=3)
    assert problem[0] == "login" and "No search results" in problem[1]


def test_handle_problem_headless_and_block_always_raise(cfg, monkeypatch):
    monkeypatch.setattr(rs, "shot", lambda page, name: Path(name))
    with pytest.raises(rs.SessionProblem):
        rs.handle_problem(FakePage(), cfg, ("login", "expired"))            # headless
    headed = rs.Config.from_env({**ENV, "REDNOTE_HEADLESS": "0"})
    with pytest.raises(rs.SessionProblem):
        rs.handle_problem(FakePage(), headed, ("block", "blocked"))         # a human can't fix an IP block


def test_headed_login_asks_the_human_instead_of_guessing(monkeypatch):
    headed = rs.Config.from_env({**ENV, "REDNOTE_HEADLESS": "0"})
    steps = []
    monkeypatch.setattr(rs, "goto_with_retry", lambda page, url, cfg: steps.append(("goto", url)))
    monkeypatch.setattr(rs, "wait_for_human", lambda msg: steps.append(("human", msg)))
    monkeypatch.setattr(rs, "page_problem", lambda page: None)  # heuristic would say "all fine"
    rs.handle_problem(FakePage(), headed, ("login", "no results"))
    assert [s[0] for s in steps] == ["goto", "human"] and steps[0][1].endswith("/explore")


def test_wait_for_human_uses_enter_on_a_terminal(monkeypatch):
    monkeypatch.setattr(rs.sys.stdin, "isatty", lambda: True)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda p="": prompts.append(p) or "")
    rs.wait_for_human("Log in")
    assert prompts and "Enter" in prompts[0]


def test_login_flag_runs_login_flow_without_keywords(monkeypatch):
    monkeypatch.setenv("KEYWORDS", "")
    monkeypatch.setattr(rs, "load_dotenv", lambda p: None)
    monkeypatch.setattr(rs, "login_flow", lambda cfg: 0)
    monkeypatch.setattr(rs.sys, "argv", ["rednote_slots.py", "--login"])
    assert rs.main() == 0


def test_logged_out_search_box_and_english_button_are_detected():
    assert rs.page_problem(FakePage(placeholder="登录探索更多内容"))[0] == "login"
    assert rs.page_problem(FakePage(texts=["Log in"]))[0] == "login"
    assert rs.page_problem(FakePage(placeholder="搜索小红书")) is None


def test_site_follows_regional_redirect():
    assert rs.origin_of("https://www.rednote.com/explore?x=1") == "https://www.rednote.com"
    assert rs.origin_of("") == rs.DEFAULT_BASE
    assert rs.search_url("美签 放号", "https://www.rednote.com").startswith(
        "https://www.rednote.com/search_result?keyword=%E7%BE%8E")
    n = rs.normalise_note({"id": "n1"}, "https://www.rednote.com")
    assert n["url"] == "https://www.rednote.com/explore/n1"
    assert rs.Config.from_env({"REDNOTE_BASE_URL": "https://www.rednote.com/"}).base_url == "https://www.rednote.com"


def test_describe_page_never_leaks_cookie_values():
    class Ctx:
        def cookies(self): return [{"name": "web_session", "value": "SECRET"}]
    out = rs.describe_page(FakePage(), {"searches": 0, "calls": ["200 /api/x"]}, Ctx())
    assert "has_web_session_cookie=True" in out and "SECRET" not in out


class Mouse:
    def __init__(self, page): self.page = page
    def wheel(self, dx, dy): self.page.scrolls += 1; self.page.on_scroll(self.page.scrolls)


class ScrollPage(FakePage):
    def __init__(self, on_scroll):
        super().__init__()
        self.scrolls, self.on_scroll, self.mouse = 0, on_scroll, Mouse(self)


def _note(i): return {"id": f"n{i}", "title": "t", "url": "u"}


def test_scroll_skipped_when_newest_results_all_seen(cfg, monkeypatch):
    monkeypatch.setattr(rs, "pause", lambda page, c: None)
    page = ScrollPage(lambda n: None)
    rs.scroll_for_new(page, [_note(1), _note(2)], lambda n: n["id"] not in {"n1", "n2"}, cfg, newest_first=True)
    assert page.scrolls == 0


def test_scroll_stops_once_it_reaches_seen_posts(cfg, monkeypatch):
    monkeypatch.setattr(rs, "pause", lambda page, c: None)
    batch = [_note(1)]
    def more(n):  # pass 1 brings a new post, pass 2 brings only already-seen ones
        batch.append(_note(10 + n) if n == 1 else _note(2))
    page = ScrollPage(more)
    rs.scroll_for_new(page, batch, lambda n: n["id"] != "n2", cfg, newest_first=True)
    assert page.scrolls == 2


def test_scroll_stops_when_no_more_results_load(cfg, monkeypatch):
    monkeypatch.setattr(rs, "pause", lambda page, c: None)
    page = ScrollPage(lambda n: None)
    rs.scroll_for_new(page, [_note(1)], lambda n: True, cfg, newest_first=False)
    assert page.scrolls == 1


def test_scroll_goes_deep_when_sort_failed(cfg, monkeypatch):
    monkeypatch.setattr(rs, "pause", lambda page, c: None)
    batch = [_note(0)]
    page = ScrollPage(lambda n: batch.append(_note(100 + n)))
    rs.scroll_for_new(page, batch, lambda n: n["id"] != "n0", cfg, newest_first=False)
    assert page.scrolls == cfg.max_scrolls


def test_notifier_caps_a_flood_into_one_summary(monkeypatch):
    sent, summaries = [], []
    monkeypatch.setattr(rs, "notify_hit", lambda h: sent.append(h["id"]))
    monkeypatch.setattr(rs, "notify_text", lambda subject, text, attachment=None: summaries.append(text))
    n = rs.Notifier(cap=2)
    for i in range(5):
        n.hit({"id": f"n{i}", "title": f"post {i}", "url": f"https://x/{i}"})
    n.flush()
    assert sent == ["n0", "n1"]
    assert len(summaries) == 1 and "3 more" in summaries[0] and "https://x/4" in summaries[0]
    n.flush()
    assert len(summaries) == 1  # nothing left to flush


def test_notifier_cap_zero_means_unlimited(monkeypatch):
    sent = []
    monkeypatch.setattr(rs, "notify_hit", lambda h: sent.append(h))
    n = rs.Notifier(cap=0)
    for i in range(50):
        n.hit({"id": str(i)})
    assert len(sent) == 50 and not n.held


from datetime import datetime, timedelta  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

NOW = datetime(2026, 11, 18, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.mark.parametrize("text,expected_date", [
    ("刚刚", "2026-11-18"),
    ("5分钟前", "2026-11-18"),
    ("3小时前", "2026-11-18"),
    ("1天前", "2026-11-17"),
    ("2天前 北京", "2026-11-16"),
    ("昨天 13:20", "2026-11-17"),
    ("编辑于 昨天 13:20 广东", "2026-11-17"),
    ("今天 09:05", "2026-11-18"),
    ("前天", "2026-11-16"),
    ("11-15", "2026-11-15"),
    ("2025-03-02", "2025-03-02"),
    ("12-30", "2025-12-30"),            # MM-DD in the future means last year
    ("2 hours ago", "2026-11-18"),
    ("3 days ago", "2026-11-15"),
    ("Yesterday 10:00", "2026-11-17"),
    ("1周前", "2026-11-11"),
])
def test_parse_posted_text(text, expected_date):
    assert rs.parse_posted_text(text, NOW).date().isoformat() == expected_date


@pytest.mark.parametrize("text", ["", None, "广东", "99-99", "13-45"])
def test_unreadable_dates_return_none(text):
    assert rs.parse_posted_text(text, NOW) is None


def test_post_datetime_falls_back_to_note_id_timestamp():
    ts = int((NOW - timedelta(days=1)).timestamp())
    note = {"id": f"{ts:08x}" + "0" * 16, "posted": ""}
    assert rs.post_datetime(note, NOW).date().isoformat() == "2026-11-17"
    assert rs.post_datetime({"id": "not-an-objectid", "posted": ""}, NOW) is None
    assert rs.post_datetime({"id": "0" * 24, "posted": ""}, NOW) is None   # timestamp 0: implausible


def test_visible_date_beats_note_id():
    ts = int((NOW - timedelta(days=30)).timestamp())
    note = {"id": f"{ts:08x}" + "0" * 16, "posted": "3小时前"}
    assert rs.post_datetime(note, NOW).date().isoformat() == "2026-11-18"


def test_too_old():
    old, yday, today = {"posted": "3天前"}, {"posted": "昨天 12:00"}, {"posted": "2小时前"}
    assert rs.too_old(old, 1, NOW) and not rs.too_old(yday, 1, NOW) and not rs.too_old(today, 1, NOW)
    assert rs.too_old(yday, 0, NOW) and not rs.too_old(today, 0, NOW)   # 0 = today only
    assert not rs.too_old(old, None, NOW)                              # off
    assert not rs.too_old({"posted": "", "id": "x"}, 1, NOW)           # unknown date: keep


def test_max_age_and_timezone_config():
    assert rs.Config.from_env({}).max_age_days == 1                    # default: today + yesterday
    assert rs.Config.from_env({"MAX_POST_AGE_DAYS": "0"}).max_age_days == 0
    assert rs.Config.from_env({"MAX_POST_AGE_DAYS": "off"}).max_age_days is None
    assert rs.Config.from_env({"MAX_POST_AGE_DAYS": "-1"}).max_age_days is None
    with pytest.raises(ValueError, match="MAX_POST_AGE_DAYS"):
        rs.Config.from_env({"MAX_POST_AGE_DAYS": "soon"})
    assert str(rs.Config.from_env({"TIMEZONE": "Asia/Shanghai"}).tz) == "Asia/Shanghai"
    with pytest.raises(ValueError, match="TIMEZONE"):
        rs.Config.from_env({"TIMEZONE": "Mars/Base"})


def test_extra_terms_add_a_required_group():
    c = rs.Config.from_env({"TOPIC_TERMS": "美签", "EVENT_TERMS": "放号", "EXTRA_TERMS": "北京,beijing,\\bBJ\\b"})
    m = lambda t: rs.is_match(t, c.topic, c.event, c.extra)  # noqa: E731
    assert m("北京美签放号了")
    assert m("Beijing 美签 放号")
    assert not m("上海美签放号了")      # right topic and event, wrong city
    assert not m("北京天气")            # right city only
    assert rs.is_match("上海美签放号了", c.topic, c.event)  # extra omitted = unchanged behaviour


def test_latin_terms_next_to_chinese_need_lookarounds_not_word_boundaries():
    wb = rs.compile_terms("T", r"\\bB-?[12]\\b")
    la = rs.compile_terms("T", r"(?<![A-Za-z0-9])B-?[12](?![0-9])")
    for title in ("今天B1放号", "B1B2放号", "美签B2面签", "b2签证", "B-1/B-2 slot", "B1 B2", "B1/B2 放号"):
        assert rs.is_match(title, la, []), title
    assert not rs.is_match("今天B1放号", wb, [])   # the trap: Chinese chars are \w, so no boundary
    for title in ("B12", "GB1", "B20", "B3"):
        assert not rs.is_match(title, la, []), title


def test_shipped_env_example_matches_b1b2_posts():
    from pathlib import Path
    env = dict(line.split("=", 1) for line in Path(rs.HERE / ".env.example").read_text().splitlines()
               if "=" in line and not line.startswith("#"))
    c = rs.Config.from_env({"TOPIC_TERMS": env["TOPIC_TERMS"], "EVENT_TERMS": env["EVENT_TERMS"]})
    assert rs.is_match("今天B1放号", c.topic, c.event)
    assert rs.is_match("B-1/B-2 new slots", c.topic, c.event)
    assert not rs.is_match("GB1放号", c.topic, c.event)
