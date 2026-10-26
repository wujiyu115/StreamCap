"""抖音推荐直播：响应裁剪、个性化判定、登录态失效降级、缓存行为。

网络层全部替换成 fake，测试不发任何真实请求。
"""
import asyncio
import contextlib
import time

import pytest

from app.core.platforms import douyin_following as df
from app.core.platforms import douyin_live_feed as feed


def slot():
    return contextlib.nullcontext()


def run(coro):
    return asyncio.run(coro)


def _item(web_rid, title="标题", status=0, user_count=1234, is_ad=False):
    return {
        "type": 1,
        "is_ad": is_ad,
        "web_rid": web_rid,
        "live_status": 0,
        "data": {
            "id_str": f"room-{web_rid}",
            "status": status,
            "title": title,
            "user_count": user_count,
            "content_tag": "",
            "cover": {"url_list": [f"http://p3-static.douyinpic.com/{web_rid}.jpg"]},
            "owner": {
                "sec_uid": f"MS4wLjABAAAA{web_rid}",
                "nickname": f"主播{web_rid}",
                "web_rid": web_rid,
                "avatar_thumb": {"url_list": [f"http://p3.douyinpic.com/{web_rid}_a.jpeg"]},
            },
            "room_view_stats": {"display_short": str(user_count)},
        },
    }


def _payload(items, extra=None):
    return {"status_code": 0, "data": items, "extra": extra or {}}


# ---------------------------------------------------------------- 归一化


def test_normalize_recommend_rooms_trims_fields():
    rooms = feed.normalize_recommend_rooms(_payload([_item("274049768117", title="演出")]))
    assert rooms is not None and len(rooms) == 1
    room = rooms[0]
    assert room["web_rid"] == "274049768117"
    assert room["room_id"] == "room-274049768117"
    assert room["title"] == "演出"
    assert room["online"] == "1234"
    assert room["status"] == 0
    # 封面/头像都要升级成 https（HTTPS 部署下 http 会被拦）
    assert room["cover"].startswith("https://")
    assert room["anchor"]["avatar"].startswith("https://")
    assert room["anchor"]["nickname"] == "主播274049768117"
    assert room["anchor"]["sec_uid"] == "MS4wLjABAAAA274049768117"


def test_normalize_recommend_rooms_drops_ad_ended_and_duplicate():
    rooms = feed.normalize_recommend_rooms(
        _payload(
            [
                _item("111", is_ad=True),  # 广告
                _item(""),  # 没短号（占位/下播项）
                _item("222", status=2),  # 不是直播中
                _item("333"),
                _item("333"),  # 重复
            ]
        )
    )
    assert [r["web_rid"] for r in rooms or []] == ["333"]


def test_normalize_recommend_rooms_shape_mismatch_is_none():
    assert feed.normalize_recommend_rooms({"status_code": 0, "data": None}) is None
    assert feed.normalize_recommend_rooms({"data": "nope"}) is None
    assert feed.normalize_recommend_rooms("nope") is None


def test_normalize_recommend_rooms_online_from_user_count_fallback():
    item = _item("444", user_count=88)
    item["data"]["room_view_stats"] = {}
    rooms = feed.normalize_recommend_rooms(_payload([item]))
    assert rooms is not None and rooms[0]["online"] == "88"


# ---------------------------------------------------------------- 个性化判定

PERSONAL_EXTRA = {"user_extra_info": '{"user_feat":{"activeness":"1"},"revenue_user_label":2,"life_layer_2":3}'}
ANON_EXTRA = {"user_extra_info": '{"revenue_user_label":-5,"life_layer_2":-1}'}


def test_is_personalized_reads_user_extra_info():
    assert feed._is_personalized(_payload([], PERSONAL_EXTRA), True) is True
    # 送了 Cookie 但平台按匿名处理（登录态失效/被降级）
    assert feed._is_personalized(_payload([], ANON_EXTRA), True) is False
    # 没送 Cookie 一律算热门榜
    assert feed._is_personalized(_payload([], PERSONAL_EXTRA), False) is False
    # 读不到就退回朴素判据
    assert feed._is_personalized(_payload([]), True) is True


# ---------------------------------------------------------------- 降级与 ttwid


@pytest.fixture
def no_ttwid_fetch(monkeypatch):
    """匿名 ttwid 不现取真网页。"""
    calls = []

    async def fake_ttwid(proxy=None):
        calls.append(proxy)
        return "anon-ttwid"

    monkeypatch.setattr(feed, "fetch_anonymous_ttwid", fake_ttwid)
    feed.reset_anonymous_ttwid()
    yield calls
    feed.reset_anonymous_ttwid()


def test_login_required_falls_back_to_anonymous(monkeypatch, no_ttwid_fetch):
    """Cookie 失效不该让整块推荐挂掉，要像网页那样退回热门榜。"""
    seen = []

    async def fake_get(cookie, proxy):
        seen.append(cookie)
        if cookie.startswith("sessionid"):
            raise df.DouyinFollowingError("login_required", "登录态失效", status_code=8)
        return _payload([_item("555")], ANON_EXTRA)

    monkeypatch.setattr(feed, "_get_feed", fake_get)
    rooms, personalized = run(feed.fetch_recommend_rooms("sessionid=dead; ttwid=x", None))
    assert [r["web_rid"] for r in rooms] == ["555"]
    assert personalized is False
    assert seen == ["sessionid=dead; ttwid=x", "ttwid=anon-ttwid"]


def test_upstream_error_propagates_when_logged_in(monkeypatch, no_ttwid_fetch):
    """非登录态类的错误照旧抛出去（别把网络故障伪装成热门榜）。"""

    async def fake_get(cookie, proxy):
        raise df.DouyinFollowingError("upstream_error", "抖音推荐直播返回 HTTP 500")

    monkeypatch.setattr(feed, "_get_feed", fake_get)
    with pytest.raises(df.DouyinFollowingError) as exc:
        run(feed.fetch_recommend_rooms("sessionid=live", None))
    assert exc.value.code == "upstream_error"


def test_anonymous_retries_with_fresh_ttwid(monkeypatch, no_ttwid_fetch):
    """缓存的 ttwid 过期（444 空响应）要换一条再试一次。"""
    attempts = []

    async def fake_get(cookie, proxy):
        attempts.append(cookie)
        if len(attempts) == 1:
            raise df.DouyinFollowingError("upstream_error", "抖音推荐直播返回 HTTP 444")
        return _payload([_item("666")], ANON_EXTRA)

    monkeypatch.setattr(feed, "_get_feed", fake_get)
    rooms, personalized = run(feed.fetch_recommend_rooms("", None))
    assert [r["web_rid"] for r in rooms] == ["666"]
    assert personalized is False
    assert len(attempts) == 2
    assert all(cookie.startswith("ttwid=") for cookie in attempts)


def test_anonymous_ttwid_is_cached(monkeypatch, no_ttwid_fetch):
    async def fake_get(cookie, proxy):
        return _payload([_item("777")], ANON_EXTRA)

    monkeypatch.setattr(feed, "_get_feed", fake_get)
    run(feed.fetch_recommend_rooms("", None))
    run(feed.fetch_recommend_rooms("", None))
    assert len(no_ttwid_fetch) == 1  # 第二次直接用缓存的那条


# ---------------------------------------------------------------- 缓存


def test_store_caches_and_refresh_bypasses(monkeypatch):
    calls = []

    async def fake_fetch(cookie, proxy):
        calls.append(cookie)
        return [{"web_rid": f"r{len(calls)}"}], True

    monkeypatch.setattr(feed, "fetch_recommend_rooms", fake_fetch)
    store = feed.RecommendStore(ttl=60)

    first = run(store.feed("sessionid=a", None, slot))
    again = run(store.feed("sessionid=a", None, slot))
    assert len(calls) == 1 and first["fetched_at"] == again["fetched_at"]
    assert first["personalized"] is True

    # 「换一批」必须真打接口
    run(store.feed("sessionid=a", None, slot, refresh=True))
    assert len(calls) == 2

    # 过期的缓存也要重取
    store.fetched_at = time.time() - 61
    run(store.feed("sessionid=a", None, slot))
    assert len(calls) == 3


def test_store_resets_when_cookie_changes(monkeypatch):
    async def fake_fetch(cookie, proxy):
        return [{"web_rid": "x"}], bool(cookie)

    monkeypatch.setattr(feed, "fetch_recommend_rooms", fake_fetch)
    store = feed.RecommendStore(ttl=60)
    run(store.feed("sessionid=a", None, slot))
    assert store.personalized is True
    run(store.feed("", None, slot))
    assert store.personalized is False  # 换了 Cookie（含清空）后不能沿用上一批