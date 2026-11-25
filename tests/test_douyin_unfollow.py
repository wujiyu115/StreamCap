"""抖音「取消关注」写操作：纯逻辑（头像键/目标匹配/query 清洗）+ 接口层错误码映射。

浏览器全部换成假的，测试不起 playwright、不发任何请求。
"""
import asyncio

import pytest

from app.core.platforms import douyin_write as dw
from app.server import error_codes as errors
from app.server.routers import douyin as douyin_router
from app.server.schemas import DouyinUnfollowRequest

WWW_AVATAR = (
    "https://p3-pc.douyinpic.com/aweme/100x100/aweme-avatar/"
    "tos-cn-avt-0015_7a0373ff2ef0a7ea93782c992a548d50.jpeg?from=2956013662"
)
CREATOR_AVATAR = "aweme-avatar/tos-cn-avt-0015_7a0373ff2ef0a7ea93782c992a548d50"


# ---------------------------------------------------------------- 纯逻辑


def test_avatar_key_matches_both_sides():
    """www 是完整 URL、creator 是相对路径，共同的头像 ID 才是可靠匹配键。"""
    www_key = dw.avatar_key(WWW_AVATAR)
    assert www_key == dw.avatar_key(CREATOR_AVATAR)
    assert www_key == "tos-cn-avt-0015_7a0373ff2ef0a7ea93782c992a548d50"
    assert dw.avatar_key("") == ""
    assert dw.avatar_key("https://example.com/nothing.png") == ""


def test_pick_target_prefers_avatar_then_nickname():
    rows = [
        {"nickname": "同名A", "avatar": "aweme-avatar/AAA", "user_id": "@uid-a"},
        {"nickname": "改了昵称", "avatar": CREATOR_AVATAR, "user_id": "@uid-target"},
    ]
    # 头像命中优先：昵称对不上也能找到（改昵称的常见情形）
    hit = dw.pick_target(rows, nickname="别的昵称", avatar=WWW_AVATAR)
    assert hit is not None
    assert hit["user_id"] == "@uid-target"
    # 头像都没有时退回昵称
    hit = dw.pick_target([{"nickname": "同名A", "avatar": "", "user_id": "@uid-a"}], nickname="同名A")
    assert hit is not None
    assert hit["user_id"] == "@uid-a"
    # 都对不上就不猜
    assert dw.pick_target(rows, nickname="查无此人", avatar="https://x/aweme-avatar/ZZZ.jpeg") is None
    assert dw.pick_target(None) is None


def test_list_query_drops_paging_params():
    raw = (
        "https://creator.douyin.com/aweme/v1/creator/relation/following/list/"
        "?aid=2906&app_name=aweme_creator_platform&cursor=1793630212&count=10&a_bogus=xx&msToken=yy"
    )
    query = dw._list_query(raw)
    assert "aid=2906" in query
    assert "app_name=aweme_creator_platform" in query
    assert "a_bogus=xx" in query
    assert "msToken=yy" in query
    # 翻页参数必须剔掉：写接口带 cursor/count 是没意义的
    assert "cursor=" not in query
    assert "count=" not in query
    assert dw._list_query("https://x/y") == ""


def test_unfollow_rejects_empty_sec_uid():
    with pytest.raises(dw.DouyinWriteError) as exc:
        dw.unfollow("   ")
    assert exc.value.code == "target_not_found"


class FakeSession:
    """脚本化 creator 会话：记录写请求、按预设返回回读结果。"""

    def __init__(self, following_after=False, cancel_error=None, resolve_error=None):
        self.following_after = following_after
        self.cancel_error = cancel_error
        self.resolve_error = resolve_error
        self.cancelled = []
        self._handle_cache = {}
        self._rows_ts = 0.0
        self.page_ensured = 0

    def _ensure_page(self):
        self.page_ensured += 1

    def resolve_user_id(self, nickname="", avatar=""):
        if self.resolve_error:
            raise self.resolve_error
        return "@uid-resolved"

    def cancel_follow(self, user_id):
        self.cancelled.append(user_id)
        if self.cancel_error:
            raise self.cancel_error
        return {"status_code": 0}

    def is_following(self, sec_uid):
        return self.following_after


@pytest.fixture
def fake_session(monkeypatch):
    def install(session):
        monkeypatch.setattr(dw, "_SESSION", session)
        return session

    yield install
    dw._SESSION = None


def test_unfollow_reports_verified_when_button_flipped(fake_session):
    sess = fake_session(FakeSession(following_after=False))
    out = dw.unfollow("sec-1", nickname="某人", avatar=WWW_AVATAR)
    assert out["verified"] is True
    assert sess.cancelled == ["@uid-resolved"]


def test_unfollow_raises_when_still_following(fake_session):
    """status_code 0 不等于成功：回读仍是「已关注」要报 verify_failed。"""
    fake_session(FakeSession(following_after=True))
    with pytest.raises(dw.DouyinWriteError) as exc:
        dw.unfollow("sec-1", nickname="某人")
    assert exc.value.code == "verify_failed"


def test_unfollow_passes_through_session_errors(fake_session):
    fake_session(FakeSession(resolve_error=dw.DouyinWriteError("target_not_found", "x")))
    with pytest.raises(dw.DouyinWriteError) as exc:
        dw.unfollow("sec-1", nickname="某人")
    assert exc.value.code == "target_not_found"

    fake_session(FakeSession(cancel_error=dw.DouyinWriteError("rate_limited")))
    with pytest.raises(dw.DouyinWriteError) as exc:
        dw.unfollow("sec-1", nickname="某人")
    assert exc.value.code == "rate_limited"


# ---------------------------------------------------------------- 接口层


class StubServices:
    def __init__(self, cookie="sessionid=abc"):
        class _Cfg:
            def get_cookies_value(self, _platform):
                return cookie

        self.settings_config = _Cfg()


def _call_unfollow(services, body):
    return asyncio.run(douyin_router.unfollow_user(body=body, user="tester", services=services))


def test_endpoint_maps_errors(monkeypatch):
    body = DouyinUnfollowRequest(sec_uid="sec-1", nickname="某人")
    cases = [
        ("target_not_found", 404, errors.DOUYIN_UNFOLLOW_TARGET_MISSING),
        ("rate_limited", 429, errors.DOUYIN_WRITE_RATE_LIMITED),
        ("verify_failed", 502, errors.DOUYIN_UNFOLLOW_UNVERIFIED),
        ("upstream_error", 502, errors.DOUYIN_UNFOLLOW_FAILED),
        ("login_required", 400, errors.DOUYIN_LOGIN_REQUIRED),
        ("browser_unavailable", 501, errors.DOUYIN_LOGIN_UNAVAILABLE),
    ]
    for code, status, err_code in cases:

        def boom(*_a, _code=code, **_kw):
            raise dw.DouyinWriteError(_code, "detail")

        monkeypatch.setattr(dw, "unfollow", boom)
        with pytest.raises(douyin_router.HTTPException) as exc:
            _call_unfollow(StubServices(), body)
        assert exc.value.status_code == status, code
        assert str(exc.value.detail).startswith(err_code), code


def test_endpoint_requires_cookie(monkeypatch):
    monkeypatch.setattr(dw, "unfollow", lambda *a, **k: pytest.fail("不该走到浏览器"))
    with pytest.raises(douyin_router.HTTPException) as exc:
        _call_unfollow(StubServices(cookie="  "), DouyinUnfollowRequest(sec_uid="sec-1"))
    assert exc.value.status_code == 400
    assert exc.value.detail == errors.DOUYIN_COOKIE_MISSING


def test_endpoint_success_resets_following_cache(monkeypatch):
    monkeypatch.setattr(dw, "unfollow", lambda *a, **k: {"user_id": "@u", "verified": True, "nickname": "某人"})
    resets = []
    monkeypatch.setattr(douyin_router.following.get_store(), "reset", lambda: resets.append(1))
    out = _call_unfollow(StubServices(), DouyinUnfollowRequest(sec_uid="sec-1", nickname="某人"))
    assert out == {"ok": True, "user_id": "@u", "verified": True, "nickname": "某人"}
    assert resets == [1], "取关后关注列表缓存必须作废"
