"""关注列表搜索 + 推荐直播「关注」+ 同步浏览器独占线程。

三个 2026-11-27 的事：

1. 生产取关 500 `greenlet.error: Cannot switch to a different thread`——Playwright 同步
   对象绑线程，`asyncio.to_thread` 两次可能落到池里不同线程。所有同步浏览器调用要排队到
   `browser_thread` 的唯一线程上。
2. 「全部关注」加搜索：抖音没有搜索接口，只能把游标链翻下来本地过滤。
3. 推荐直播加「关注」：点对方主页的关注按钮（www 写接口要 Argus 签名，直连 403），
   再回读按钮文案校验。
"""
import asyncio
import contextlib
import threading

import pytest

from app.core.platforms import browser_thread
from app.core.platforms import douyin_following as df
from app.core.platforms import douyin_write as dw
from app.server import error_codes as errors
from app.server.routers import douyin as douyin_router
from app.server.schemas import DouyinFollowRequest


def slot():
    return contextlib.nullcontext()


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_real_requests(monkeypatch):
    async def fake_profile(cookie, proxy):
        return {"sec_uid": "SELF", "uid": "123", "nickname": "我"}

    monkeypatch.setattr(df, "fetch_self_profile", fake_profile)


# ---------------------------------------------------------------- 搜索：纯逻辑


def test_normalize_query_trims_and_lowercases():
    assert df._normalize_query("  AbC ") == "abc"
    assert df._normalize_query(None) == ""
    assert len(df._normalize_query("万" * 200)) == 64


def test_filter_users_matches_nickname_uid_shortid():
    users = [
        {"sec_uid": "S1", "nickname": "辣椒爱健身", "unique_id": "cs818818818", "short_id": ""},
        {"sec_uid": "S2", "nickname": "兔子🐰", "unique_id": "Rabbit", "short_id": "4238"},
        {"sec_uid": "S3", "nickname": "路人", "unique_id": "", "short_id": ""},
    ]
    assert [u["sec_uid"] for u in df._filter_users(users, "辣椒")] == ["S1"]
    assert [u["sec_uid"] for u in df._filter_users(users, "rabbit")] == ["S2"]  # 大小写不敏感
    assert [u["sec_uid"] for u in df._filter_users(users, "4238")] == ["S2"]
    assert [u["sec_uid"] for u in df._filter_users(users, "s3")] == ["S3"]  # sec_uid 兜底
    assert df._filter_users(users, "") == users
    assert df._filter_users(users, "查无此人") == []


# ---------------------------------------------------------------- 搜索：游标链


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    async def __call__(self, cookie, proxy, *, max_time, page_size, first=False, sec_uid=None, uid=None):
        self.calls.append(max_time)
        return df.normalize_following_page(self.pages[max_time])


def _page(names, max_time, has_more, min_time=0):
    return {
        "status_code": 0,
        "followings": [{"sec_uid": f"S-{n}", "nickname": n} for n in names],
        "has_more": has_more,
        "max_time": max_time,
        "min_time": min_time,
        "total": 6,
    }


def test_store_search_walks_until_match_found(monkeypatch):
    """搜索要在已加载的页里过滤，没找够就继续顺游标翻（每请求有上限）。"""
    fetcher = FakeFetcher(
        {
            0: _page(["阿甲", "阿乙"], 999, 1, min_time=100),
            100: _page(["辣椒爱健身", "阿丙"], 999, 0, min_time=0),
        }
    )
    monkeypatch.setattr(df, "fetch_following_page", fetcher)
    store = df.FollowingStore()

    out = run(store.following_page("cookie", None, slot, page=1, page_size=20, query="辣椒"))

    assert [u["nickname"] for u in out["users"]] == ["辣椒爱健身"]
    assert out["matched_total"] == 1
    assert out["query"] == "辣椒"
    assert out["has_more"] is False       # 已翻到底
    assert out["total"] == 6              # 关注总数仍是平台的 total
    assert fetcher.calls == [0, 100]


def test_store_search_matches_loaded_pages_without_refetch(monkeypatch):
    fetcher = FakeFetcher({0: _page(["阿甲", "阿乙"], 999, 1, min_time=100), 100: _page(["阿丙"], 0, 0)})
    monkeypatch.setattr(df, "fetch_following_page", fetcher)
    store = df.FollowingStore()

    async def scenario():
        await store.following_page("cookie", None, slot, page=1, page_size=50)  # 先把列表翻到底
        calls_after = len(fetcher.calls)
        hit = await store.following_page("cookie", None, slot, page=1, page_size=50, query="阿乙")
        return calls_after, hit

    calls_after, hit = run(scenario())
    assert [u["nickname"] for u in hit["users"]] == ["阿乙"]
    assert len(fetcher.calls) == calls_after, "已加载页的搜索不该再出网"


def test_store_search_paginates_matches(monkeypatch):
    names = ["辣椒1", "路人A", "辣椒2", "路人B", "辣椒3"]
    fetcher = FakeFetcher({0: _page(names, 0, 0)})
    monkeypatch.setattr(df, "fetch_following_page", fetcher)
    store = df.FollowingStore()

    async def scenario():
        first = await store.following_page("cookie", None, slot, page=1, page_size=2, query="辣椒")
        second = await store.following_page("cookie", None, slot, page=2, page_size=2, query="辣椒")
        return first, second

    first, second = run(scenario())
    assert [u["nickname"] for u in first["users"]] == ["辣椒1", "辣椒2"]
    assert first["matched_total"] == 3
    assert first["has_more"] is True
    assert [u["nickname"] for u in second["users"]] == ["辣椒3"]
    assert second["has_more"] is False


def test_store_without_query_keeps_old_shape(monkeypatch):
    fetcher = FakeFetcher({0: _page(["阿甲"], 0, 0)})
    monkeypatch.setattr(df, "fetch_following_page", fetcher)
    store = df.FollowingStore()
    out = run(store.following_page("cookie", None, slot, page=1, page_size=2))
    assert out["query"] == ""
    assert out["matched_total"] is None
    assert [u["nickname"] for u in out["users"]] == ["阿甲"]


# ---------------------------------------------------------------- 关注：写操作


class FakeFollowSession:
    """脚本化 creator 会话：只实现 follow 用到的几个方法。"""

    def __init__(self, follow_ok=True, follow_error=None):
        self.follow_ok = follow_ok
        self.follow_error = follow_error
        self.called = []
        self._rows_ts = 123.0

    def _ensure_page(self):
        self.called.append("ensure")

    def follow(self, sec_uid):
        self.called.append(("follow", sec_uid))
        if self.follow_error:
            raise self.follow_error
        return self.follow_ok


@pytest.fixture
def fake_session(monkeypatch):
    def install(session):
        monkeypatch.setattr(dw, "_SESSION", session)
        return session

    yield install
    dw._SESSION = None


def test_follow_rejects_empty_sec_uid():
    with pytest.raises(dw.DouyinWriteError) as exc:
        dw.follow("  ")
    assert exc.value.code == "target_not_found"


def test_follow_verified(fake_session):
    sess = fake_session(FakeFollowSession(follow_ok=True))
    out = dw.follow("sec-1", nickname="兔子")
    assert out["verified"] is True
    assert sess.called == ["ensure", ("follow", "sec-1")]
    assert sess._rows_ts == 0.0, "关注关系变了，句柄映射必须作废"


def test_follow_unverified_raises(fake_session):
    fake_session(FakeFollowSession(follow_ok=False))
    with pytest.raises(dw.DouyinWriteError) as exc:
        dw.follow("sec-1")
    assert exc.value.code == "verify_failed"


def test_follow_passes_through_session_errors(fake_session):
    fake_session(FakeFollowSession(follow_error=dw.DouyinWriteError("rate_limited", "captcha")))
    with pytest.raises(dw.DouyinWriteError) as exc:
        dw.follow("sec-1")
    assert exc.value.code == "rate_limited"


def test_is_followed_labels():
    assert dw._is_followed("已关注")
    assert dw._is_followed(" 互相关注 ")
    assert dw._is_followed("相互关注")
    assert not dw._is_followed("关注")
    assert not dw._is_followed("")


def test_ensure_page_rebuilds_browser_when_thread_moved(monkeypatch):
    """老路径（to_thread 换了池线程）的兜底：认出换了线程就把浏览器重建，别让 greenlet 炸。"""
    session = dw.CreatorSession.__new__(dw.CreatorSession)
    session._thread_id = threading.get_ident() + 1  # 假装浏览器是别的线程建的
    session._page = object()
    session._query = "aid=2906"
    session._last_used = 1e12  # 没过期，正常情况会直接复用
    closed = []

    def fake_close():
        closed.append(1)
        session._page = None   # 真 close() 会把页面/上下文都清掉

    monkeypatch.setattr(session, "close", fake_close)

    def boom():
        raise dw.DouyinWriteError("browser_unavailable", "relaunch on this thread")

    monkeypatch.setattr(session, "_launch", boom)

    with pytest.raises(dw.DouyinWriteError) as exc:
        session._ensure_page()
    assert exc.value.code == "browser_unavailable"
    assert closed == [1], "换线程时必须先关掉旧浏览器"


# ---------------------------------------------------------------- 独占线程


def test_browser_thread_uses_one_thread_for_every_call():
    seen = []
    for _ in range(3):
        seen.append(browser_thread.run_sync(threading.get_ident))
    assert len(set(seen)) == 1, "所有同步浏览器调用必须落在同一条线程上"
    assert seen[0] != threading.get_ident(), "不能在调用方线程上直接跑（Playwright 会绑死）"


def test_browser_thread_run_async_offloads_and_keeps_thread():
    async def scenario():
        before = browser_thread.run_sync(threading.get_ident)
        after = await browser_thread.run_async(threading.get_ident)
        loop_thread = threading.get_ident()
        return before, after, loop_thread

    before, after, loop_thread = run(scenario())
    assert after == before, "异步入口也要落到同一条独占线程"
    assert after != loop_thread


# ---------------------------------------------------------------- 接口层


class StubServices:
    def __init__(self, cookie="sessionid=abc"):
        class _Cfg:
            def get_cookies_value(self, _platform):
                return cookie

        self.settings_config = _Cfg()


def _call_follow(services, body):
    return asyncio.run(douyin_router.follow_user(body=body, user="tester", services=services))


@pytest.mark.parametrize(
    ("code", "status", "err_code"),
    [
        ("target_not_found", 404, errors.DOUYIN_FOLLOW_FAILED),
        ("rate_limited", 429, errors.DOUYIN_WRITE_RATE_LIMITED),
        ("verify_failed", 502, errors.DOUYIN_UNFOLLOW_UNVERIFIED),
        ("upstream_error", 502, errors.DOUYIN_FOLLOW_FAILED),
        ("login_required", 400, errors.DOUYIN_LOGIN_REQUIRED),
        ("browser_unavailable", 501, errors.DOUYIN_LOGIN_UNAVAILABLE),
    ],
)
def test_follow_endpoint_maps_errors(monkeypatch, code, status, err_code):
    def boom(*_a, **_kw):
        raise dw.DouyinWriteError(code, "detail")

    monkeypatch.setattr(dw, "follow", boom)
    with pytest.raises(douyin_router.HTTPException) as exc:
        _call_follow(StubServices(), DouyinFollowRequest(sec_uid="sec-1"))
    assert exc.value.status_code == status
    assert str(exc.value.detail).startswith(err_code)


def test_follow_endpoint_requires_cookie(monkeypatch):
    monkeypatch.setattr(dw, "follow", lambda *a, **k: pytest.fail("不该走到浏览器"))
    with pytest.raises(douyin_router.HTTPException) as exc:
        _call_follow(StubServices(cookie="  "), DouyinFollowRequest(sec_uid="sec-1"))
    assert exc.value.status_code == 400
    assert exc.value.detail == errors.DOUYIN_COOKIE_MISSING


def test_follow_endpoint_success_resets_following_cache(monkeypatch):
    monkeypatch.setattr(dw, "follow", lambda *a, **k: {"verified": True, "nickname": "兔子"})
    resets = []
    monkeypatch.setattr(douyin_router.following.get_store(), "reset", lambda: resets.append(1))
    out = _call_follow(StubServices(), DouyinFollowRequest(sec_uid="sec-1", nickname="兔子"))
    assert out == {"ok": True, "verified": True, "nickname": "兔子"}
    assert resets == [1], "关注后关注列表缓存必须作废"