import json
from types import SimpleNamespace

from app.messages.cookie_health import CookieHealthMonitor


def test_expired_cookie_notifies_enabled_channels_once_across_restart(tmp_path):
    (tmp_path / "config").mkdir()
    settings = SimpleNamespace(
        user_config={"bark_enabled": True},
        get_cookies_value=lambda _: "sid_guard=secret%7C1000%7C1000%7Cdate",
    )
    monitor = CookieHealthMonitor(str(tmp_path), settings)
    sent = []
    monitor.pusher.push_messages_sync = lambda title, content: sent.append((title, content))
    monitor.check()
    monitor.check()
    assert len(sent) == 1
    assert "secret" not in sent[0][1]
    stored = json.loads((tmp_path / "config/cookie_health.json").read_text())
    assert "secret" not in str(stored)
    restarted = CookieHealthMonitor(str(tmp_path), settings)
    restarted.pusher.push_messages_sync = lambda *args: sent.append(args)
    restarted.check()
    assert len(sent) == 1


def test_missing_unknown_or_no_channels_do_not_notify(tmp_path):
    (tmp_path / "config").mkdir()
    cookie = [""]
    settings = SimpleNamespace(user_config={}, get_cookies_value=lambda _: cookie[0])
    monitor = CookieHealthMonitor(str(tmp_path), settings)
    sent = []
    monitor.pusher.push_messages_sync = lambda *args: sent.append(args)
    monitor.check()
    cookie[0] = "ttwid=public"
    monitor.check()
    cookie[0] = "sid_guard=secret|1000|1000|date"
    monitor.check()
    assert sent == []
    assert monitor.notified == {}
