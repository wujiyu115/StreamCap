"""主播主页跳转：URL → 平台/房间号解析、缓存、降级 + 接口层。

网络调用全部替换成假的，测试不发任何真实请求。
"""
import asyncio

import pytest

from app.core.platforms import anchor_profile as ap
from app.server import error_codes as errors
from app.server.routers import recordings as rec_router


@pytest.fixture(autouse=True)
def clean_cache():
    ap.clear_cache()
    yield
    ap.clear_cache()


# ---------------------------------------------------------------- 纯逻辑


def test_room_id_extraction():
    assert ap.douyin_web_rid("https://live.douyin.com/wwen808") == "wwen808"
    assert ap.douyin_web_rid("https://live.douyin.com/154904194/") == "154904194"
    assert ap.douyin_web_rid("https://live.douyin.com/Anny_0901?x=1") == "Anny_0901"
    assert ap.douyin_web_rid("https://live.bilibili.com/123") is None
    assert ap.bilibili_room_id("https://live.bilibili.com/123?from=x") == "123"
    assert ap.bilibili_room_id("https://live.douyin.com/wwen808") is None


def test_platform_key_of():
    assert ap.platform_key_of("https://live.douyin.com/wwen808") == "douyin"
    assert ap.platform_key_of("https://live.bilibili.com/123") == "bilibili"
    assert ap.platform_key_of("https://www.huya.com/abc") == "huya"
    assert ap.platform_key_of("https://example.com/whatever") is None


def test_resolve_douyin_uses_sec_uid(monkeypatch):
    seen = []

    async def fake(url, proxy, cookies):
        seen.append((url, cookies))
        return "https://www.douyin.com/user/MS4wLjABAAAAtest"

    monkeypatch.setattr(ap, "_resolve_douyin", fake)
    link = asyncio.run(
        ap.resolve_profile_url("https://live.douyin.com/wwen808", cookies="sessionid=x")
    )
    assert link.kind == "profile"
    assert link.url == "https://www.douyin.com/user/MS4wLjABAAAAtest"
    assert link.cached is False
    assert seen == [("https://live.douyin.com/wwen808", "sessionid=x")]


def test_cache_hits_once(monkeypatch):
    calls = []

    async def fake(url, proxy, cookies):
        calls.append(url)
        return "https://www.douyin.com/user/MS4wAAAA"

    monkeypatch.setattr(ap, "_resolve_douyin", fake)
    url = "https://live.douyin.com/wwen808"
    first = asyncio.run(ap.resolve_profile_url(url))
    second = asyncio.run(ap.resolve_profile_url(url))
    assert first.kind == "profile"
    assert first.cached is False
    assert second.kind == "profile"
    assert second.cached is True
    assert calls == [url], "第二次点击必须走缓存，不再打接口"


def test_failure_falls_back_to_room(monkeypatch):
    async def boom(url, proxy, cookies):
        raise RuntimeError("network down")

    monkeypatch.setattr(ap, "_resolve_douyin", boom)
    link = asyncio.run(ap.resolve_profile_url("https://live.douyin.com/wwen808"))
    assert link.kind == "room"
    assert link.url == "https://live.douyin.com/wwen808"
    assert "network down" in (link.reason or "")


def test_unresolved_falls_back_to_room(monkeypatch):
    async def none(url, proxy, cookies):
        return None

    monkeypatch.setattr(ap, "_resolve_douyin", none)
    link = asyncio.run(ap.resolve_profile_url("https://live.douyin.com/wwen808"))
    assert link.kind == "room"
    assert link.reason == "unresolved"


def test_other_platforms_use_room_url_without_network(monkeypatch):
    async def fail(*_a, **_kw):
        pytest.fail("非抖音/B 站不该发请求")

    monkeypatch.setattr(ap, "_resolve_douyin", fail)
    monkeypatch.setattr(ap, "_resolve_bilibili", fail)
    link = asyncio.run(ap.resolve_profile_url("https://www.huya.com/abc"))
    assert link.kind == "room"
    assert link.url == "https://www.huya.com/abc"


def test_bilibili_resolves_uid(monkeypatch):
    async def fake(url, proxy):
        return "https://space.bilibili.com/12345"

    monkeypatch.setattr(ap, "_resolve_bilibili", fake)
    link = asyncio.run(ap.resolve_profile_url("https://live.bilibili.com/999"))
    assert link.kind == "profile"
    assert link.url == "https://space.bilibili.com/12345"


# ---------------------------------------------------------------- 接口层


class FakeSettings:
    user_config = {"enable_proxy": False}

    def get_cookies_value(self, platform):
        return "sessionid=abc" if platform == "douyin" else ""


class BackgroundLoop:
    """真·后台 loop（独立线程），和 app 里 services.run_coro 的语义一致。"""

    def __init__(self):
        import threading

        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def run_coro(self, coro):
        if coro is None:
            return None
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()


class FakeRecording:
    def __init__(self, rec_id, url):
        self.rec_id = rec_id
        self.url = url


class FakeServices:
    def __init__(self, recording=None):
        outer = self

        class _RM:
            def find_recording_by_id(self, rec_id):
                rec = outer.recording
                return rec if rec is not None and rec.rec_id == rec_id else None

            def platform_slot(self, _key, priority=True):
                raise AssertionError("测试里不该真进节奏门")

        self.settings_config = FakeSettings()
        self.recording = recording
        self.recording_manager = _RM()
        self.bg = None

    def run_coro(self, coro):
        return self.bg.run_coro(coro) if self.bg is not None else None


def _services(url="https://live.douyin.com/wwen808", rec_id="rec-1"):
    services = FakeServices(FakeRecording(rec_id, url))
    services.bg = BackgroundLoop()
    return services


def _call(services, rec_id="rec-1"):
    try:
        return asyncio.run(
            rec_router.get_recording_profile_url(rec_id=rec_id, user="tester", services=services)
        )
    finally:
        if services.bg is not None:
            services.bg.close()


def test_endpoint_404_when_recording_missing():
    services = _services()
    with pytest.raises(rec_router.HTTPException) as exc:
        _call(services, rec_id="nope")
    assert exc.value.status_code == 404
    assert exc.value.detail == errors.RECORDING_NOT_FOUND


def test_endpoint_degrades_when_backend_loop_unavailable():
    """run_coro 拿不到后台 loop（返回 None）时降级成直播间地址，不报 500。"""
    services = FakeServices(FakeRecording("rec-1", "https://live.douyin.com/wwen808"))
    out = _call(services)
    assert out["kind"] == "room"
    assert out["url"] == "https://live.douyin.com/wwen808"
    assert out["platform_key"] == "douyin"


def test_endpoint_passes_link_through(monkeypatch):
    async def fake_resolve(url, proxy=None, cookies=None, slot=None):
        return ap.ProfileLink(url="https://www.douyin.com/user/MS4wAAAA", kind="profile")

    monkeypatch.setattr(ap, "resolve_profile_url", fake_resolve)
    services = _services()
    out = _call(services)
    assert out["kind"] == "profile"
    assert out["url"] == "https://www.douyin.com/user/MS4wAAAA"
    assert out["platform_key"] == "douyin"


def test_endpoint_survives_resolve_exception(monkeypatch):
    async def boom(url, proxy=None, cookies=None, slot=None):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(ap, "resolve_profile_url", boom)
    services = _services()
    out = _call(services)
    assert out["kind"] == "room"
    assert out["url"] == "https://live.douyin.com/wwen808"
