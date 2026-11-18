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
