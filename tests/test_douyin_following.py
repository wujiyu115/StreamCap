"""抖音关注列表：响应归一化、游标翻页缓存、错误码分类。

网络层全部替换成 fake，测试不发任何真实请求（真机验证另见 AGENTS.md 的验证流程）。
"""
import asyncio
import contextlib
import time

import pytest

from app.core.platforms import douyin_following as df
from app.server import error_codes as errors
from app.server.routers import douyin as douyin_router

# 下面的 autouse fixture 会把 fetch_self_profile 换掉，这两个用例要测真的那个
_REAL_FETCH_SELF_PROFILE = df.fetch_self_profile


def slot():
    """节奏门的替身：nullcontext 支持 async with。"""
    return contextlib.nullcontext()


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_real_requests(monkeypatch):
    """兜底：单测不准真去打抖音（翻页前会先确保拿到自己的账号资料）。"""

    async def fake_profile(cookie, proxy):
        return {"sec_uid": "SELF", "uid": "123", "nickname": "我"}

    monkeypatch.setattr(df, "fetch_self_profile", fake_profile)


# ---------------------------------------------------------------- 归一化

SELF_PAYLOAD = {
    "status_code": 0,
    "user": {
        "sec_uid": "MS4wLjABAAAAself",
        "uid": "123",
        "nickname": "我",
        "unique_id": "me_1",
        "short_id": "0",
        "avatar_thumb": {"url_list": ["http://p3.douyinpic.com/a.jpeg"]},
        "follower_count": 12,
        "following_count": 4321,
        "aweme_count": 7,
    },
}


def test_normalize_self_profile_happy_path():
    profile = df.normalize_self_profile(SELF_PAYLOAD)
    assert profile["sec_uid"] == "MS4wLjABAAAAself"
    assert profile["nickname"] == "我"
    assert profile["unique_id"] == "me_1"
    assert profile["avatar"] == "https://p3.douyinpic.com/a.jpeg"
    assert profile["following_count"] == 4321


def test_normalize_self_profile_without_sec_uid_is_none():
    assert df.normalize_self_profile({"status_code": 0, "user": {"nickname": "x"}}) is None
    assert df.normalize_self_profile({"status_code": 0}) is None


FOLLOWING_PAYLOAD = {
    "status_code": 0,
    "followings": [
        {
            "sec_uid": "MS4wLjABAAAAa",
            "uid": "1",
            "nickname": " 主播A ",
            "unique_id": "",
            "short_id": "123456",
            "signature": "签名",
            "avatar_thumb": {"url_list": ["https://a.jpg"]},
            "follower_count": "1000",
            "aweme_count": 3,
            "custom_verify": "某某官方",
            "room_id": "730000000000",
        },
        {
            "sec_uid": "MS4wLjABAAAAb",
            "nickname": "主播B",
            "unique_id": "unique_b",
            "short_id": "0",
            "is_verified": True,
        },
        {"nickname": "没有 sec_uid 的脏数据"},
    ],
    "has_more": 1,
    "max_time": "1699999999",
    "min_time": "1699000000",
    "total": 88,
}


def test_normalize_following_page():
    page = df.normalize_following_page(FOLLOWING_PAYLOAD)
    assert page["has_more"] is True
    assert page["max_time"] == 1699999999
    assert page["min_time"] == 1699000000  # 下一页游标用它，不是 max_time
    assert page["total"] == 88
    assert [u["sec_uid"] for u in page["users"]] == ["MS4wLjABAAAAa", "MS4wLjABAAAAb"]
    first, second = page["users"]
    assert first["nickname"] == "主播A"  # 去掉首尾空白
    assert first["unique_id"] == "123456"  # unique_id 为空时回落到 short_id
    assert first["follower_count"] == 1000  # 字符串数字也认
    assert first["verified"] is True  # custom_verify 也算认证
    assert first["room_id"] == "730000000000"
    assert second["short_id"] == ""  # "0" 表示没有短号
    assert second["verified"] is True


def test_normalize_following_page_tolerates_garbage():
    assert df.normalize_following_page({}) == {
        "users": [],
        "has_more": False,
        "max_time": 0,
        "min_time": 0,
        "total": None,
    }
    assert df.normalize_following_page({"followings": None, "has_more": "0"})["users"] == []


def test_normalize_following_page_accepts_follow_list_key_and_nested_user():
    """数组键变过（follow_list）；有的版本用户对象套在 item.user 里。"""
    page = df.normalize_following_page(
        {"status_code": 0, "follow_list": [{"user": {"sec_uid": "s1", "nickname": "A"}, "foo": 1}]}
    )
    assert [u["sec_uid"] for u in page["users"]] == ["s1"]
    assert page["users"][0]["nickname"] == "A"


LIVE_PAYLOAD = {
    "data": {
        "data": [
            {
                "web_rid": "730000000001",
                "tag_name": "王者荣耀",
                "room": {
                    "id_str": "730000000001",
                    "title": "开播了",
                    "status": 2,
                    "cover": {"url_list": ["http://cover.jpg"]},
                    "room_view_stats": {"display_short": "1.2w"},
                    "owner": {
                        "sec_uid": "MS4wLjABAAAAa",
                        "nickname": "主播A",
                        "avatar_thumb": {"url_list": ["https://a.jpg"]},
                        "web_rid": "730000000001",
                    },
                },
            },
            {  # owner.web_rid 兜底 + 下播（status=4）要过滤
                "room": {"status": 4, "owner": {"nickname": "下播的", "web_rid": "888"}},
            },
            {"web_rid": "room?id=1", "room": {"status": 2}},  # 非房间号脏数据
            "字符串脏数据",
        ],
        "status_code": 0,
    },
    "status_code": 0,
}


def test_normalize_live_rooms():
    rooms = df.normalize_live_rooms(LIVE_PAYLOAD)
    assert len(rooms) == 1
    room = rooms[0]
    assert room["web_rid"] == "730000000001"
    assert room["title"] == "开播了"
    assert room["anchor"]["sec_uid"] == "MS4wLjABAAAAa"
    assert room["anchor"]["nickname"] == "主播A"
    assert room["cover"] == "https://cover.jpg"
    assert room["online"] == "1.2万"  # w → 万
    assert room["tag"] == "王者荣耀"


def test_normalize_live_rooms_shape_mismatch_is_none():
    assert df.normalize_live_rooms({"status_code": 0}) is None
    assert df.normalize_live_rooms({"data": {"data": None}}) is None
    assert df.normalize_live_rooms({"data": {"data": []}}) == []


# ---------------------------------------------------------------- 错误码分类


@pytest.mark.parametrize("status_code", [8, 20003, 20005])
def test_check_payload_maps_login_codes(status_code):
    with pytest.raises(df.DouyinFollowingError) as exc:
        df._check_payload({"status_code": status_code})
    assert exc.value.code == "login_required"
    assert exc.value.status_code == status_code


def test_check_payload_maps_other_status_and_passes_zero():
    with pytest.raises(df.DouyinFollowingError) as exc:
        df._check_payload({"status_code": 18, "status_msg": "系统繁忙"})
    assert exc.value.code == "upstream_error"
    assert "系统繁忙" in exc.value.message
    assert df._check_payload({"status_code": 0}) == {"status_code": 0}


def test_check_payload_rejects_non_dict():
    with pytest.raises(df.DouyinFollowingError) as exc:
        df._check_payload(["nope"])
    assert exc.value.code == "bad_response"


def test_self_profile_null_user_is_login_required(monkeypatch):
    """登录态没了时抖音回 ``status_code=0`` 但 ``user`` 为 null——必须报 login_required。

    报成 bad_response（上游异常）的话，前端只会显示「抖音接口异常」，不会引导去
    重新扫码登录（DDr 的 verifyIdentity 也是按 user 是否为空判登录态的）。
    """
    monkeypatch.setattr(df, "fetch_self_profile", _REAL_FETCH_SELF_PROFILE)

    async def fake_request(url, params, cookie, proxy):
        return {"status_code": 0, "user": None}

    monkeypatch.setattr(df, "_request_json", fake_request)
    with pytest.raises(df.DouyinFollowingError) as exc:
        run(df.fetch_self_profile("cookie", None))
    assert exc.value.code == "login_required"


def test_self_profile_missing_sec_uid_stays_bad_response(monkeypatch):
    """user 在场只是字段缺了，是上游结构问题，不该误报成登录态失效。"""
    monkeypatch.setattr(df, "fetch_self_profile", _REAL_FETCH_SELF_PROFILE)

    async def fake_request(url, params, cookie, proxy):
        return {"status_code": 0, "user": {"uid": "1"}}

    monkeypatch.setattr(df, "_request_json", fake_request)
    with pytest.raises(df.DouyinFollowingError) as exc:
        run(df.fetch_self_profile("cookie", None))
    assert exc.value.code == "bad_response"


def test_router_error_mapping():
    def detail(exc):
        return douyin_router._http_error(exc).detail

    login = douyin_router._http_error(df.DouyinFollowingError("login_required", "登录态失效"))
    assert login.status_code == 400
    assert login.detail == f"{errors.DOUYIN_LOGIN_REQUIRED}|登录态失效"

    upstream = douyin_router._http_error(df.DouyinFollowingError("bad_response", ""))
    assert upstream.status_code == 502
    assert upstream.detail == errors.DOUYIN_UPSTREAM_ERROR

    unknown = douyin_router._http_error(df.DouyinFollowingError("whatever", "x"))
    assert unknown.status_code == 502


def test_dispatch_maps_unexpected_and_missing_loop():
    """后台 loop 上的意外异常 / 没有 loop 可提交，都要给前端一个可翻译的错误码。"""
    import concurrent.futures
    from fastapi import HTTPException

    class FakeServices:
        def __init__(self, future):
            self._future = future

        def run_coro(self, coro):
            return self._future

    async def boom():
        raise RuntimeError("worker exploded")

    failed = concurrent.futures.Future()
    failed.set_exception(RuntimeError("worker exploded"))
    coro = boom()
    try:
        with pytest.raises(HTTPException) as exc:
            run(douyin_router._dispatch(FakeServices(failed), coro))
    finally:
        coro.close()
    assert exc.value.status_code == 502
    assert exc.value.detail.startswith(errors.DOUYIN_UPSTREAM_ERROR)

    coro = boom()
    try:
        with pytest.raises(HTTPException) as exc:
            run(douyin_router._dispatch(FakeServices(None), coro))
    finally:
        coro.close()
    assert exc.value.status_code == 500
    assert exc.value.detail == errors.DOUYIN_FOLLOWING_UNAVAILABLE


# ---------------------------------------------------------------- 接口参数约定


def test_fetch_following_page_handshake(monkeypatch):
    """第一页先 source_type=2 握手；握手不带数据就用它的 max_time 取 source_type=1。"""
    calls = []

    async def fake_request(url, params, cookie, proxy):
        calls.append((url, dict(params)))
        if params["source_type"] == 2:
            return {"status_code": 0, "followings": None, "has_more": 1, "max_time": 1700000000, "total": 5}
        return {
            "status_code": 0,
            "followings": [{"sec_uid": "u1", "nickname": "A"}],
            "has_more": 0,
            "max_time": 0,
            "total": 5,
        }

    monkeypatch.setattr(df, "_request_json", fake_request)
    page = run(df.fetch_following_page("cookie", None, max_time=0, page_size=20, first=True))

    assert len(calls) == 2
    assert calls[0][0] == df.FOLLOWING_LIST_URL
    assert calls[0][1]["source_type"] == 2 and calls[0][1]["max_time"] == 0
    assert calls[1][1]["source_type"] == 1 and calls[1][1]["max_time"] == 1700000000
    assert [u["sec_uid"] for u in page["users"]] == ["u1"]


def test_fetch_following_page_uses_handshake_data_when_present(monkeypatch):
    calls = []

    async def fake_request(url, params, cookie, proxy):
        calls.append(params["source_type"])
        return {"status_code": 0, "followings": [{"sec_uid": "u1"}], "has_more": 1, "max_time": 1, "total": 1}

    monkeypatch.setattr(df, "_request_json", fake_request)
    page = run(df.fetch_following_page("cookie", None, max_time=0, page_size=20, first=True))
    assert calls == [2]  # 握手已经带数据，不再补一次
    assert page["users"]


def test_fetch_following_page_carries_own_identity_params(monkeypatch):
    """接口要带自己的 sec_user_id / uid，否则返回空；min_time/offset 也按网页端带齐。"""
    calls = []

    async def fake_request(url, params, cookie, proxy):
        calls.append(dict(params))
        return {"status_code": 0, "followings": [], "has_more": 0, "max_time": 0, "total": 0}

    monkeypatch.setattr(df, "_request_json", fake_request)
    run(df.fetch_following_page("cookie", None, max_time=0, page_size=20, first=True, sec_uid="SELF", uid="123"))
    assert len(calls) == 1  # 握手没数据且没有游标 → 不再补请求
    assert calls[0]["sec_user_id"] == "SELF"
    assert calls[0]["user_id"] == "123"
    assert calls[0]["min_time"] == 0 and calls[0]["offset"] == 0

    calls.clear()
    run(df.fetch_following_page("cookie", None, max_time=0, page_size=20))
    assert "sec_user_id" not in calls[0] and "user_id" not in calls[0]  # 拿不到身份时不硬凑


def test_build_signed_url_carries_a_bogus_and_device_params():
    url, headers = df._build_signed_url(df.FOLLOWING_LIST_URL, {**df.WEB_DEVICE_PARAMS, "max_time": 0})
    assert url.startswith(df.FOLLOWING_LIST_URL + "?")
    assert "a_bogus=" in url
    assert "device_platform=webapp" in url
    assert headers["referer"] == "https://www.douyin.com/"


# ---------------------------------------------------------------- 游标链缓存


class FakeFetcher:
    """按 max_time 返回不同页；记录调用次数。"""

    def __init__(self, pages):
        self.pages = pages  # max_time -> payload
        self.calls = []

    async def __call__(self, cookie, proxy, *, max_time, page_size, first=False, sec_uid=None, uid=None):
        self.calls.append((cookie, max_time, page_size, first))
        payload = self.pages[max_time]
        return df.normalize_following_page(payload)


def _page(uids, max_time, has_more, min_time=0):
    return {
        "status_code": 0,
        "followings": [{"sec_uid": u, "nickname": u} for u in uids],
        "has_more": has_more,
        "max_time": max_time,
        "min_time": min_time,
        "total": 4,
    }


def test_store_walks_cursor_chain_and_serves_cached_pages(monkeypatch):
    # 第 1 页故意把 max_time 写成一个「不是游标」的值，逼出「下一页用 min_time」这条规则
    fetcher = FakeFetcher({0: _page(["u1", "u2"], 999999, 1, min_time=100), 100: _page(["u3", "u4"], 0, 0)})
    monkeypatch.setattr(df, "fetch_following_page", fetcher)
    store = df.FollowingStore()

    async def scenario():
        first = await store.following_page("cookie", None, slot, page=1, page_size=2)
        second = await store.following_page("cookie", None, slot, page=2, page_size=2)
        again = await store.following_page("cookie", None, slot, page=1, page_size=2)  # 命中缓存
        beyond = await store.following_page("cookie", None, slot, page=5, page_size=2)  # 越界回落到最后一页
        return first, second, again, beyond

    first, second, again, beyond = run(scenario())

    assert [u["sec_uid"] for u in first["users"]] == ["u1", "u2"]
    assert first["has_more"] is True and first["total"] == 4
    assert [u["sec_uid"] for u in second["users"]] == ["u3", "u4"]
    assert second["has_more"] is False  # 尾页
    assert [u["sec_uid"] for u in again["users"]] == ["u1", "u2"]
    assert [u["sec_uid"] for u in beyond["users"]] == ["u3", "u4"]
    # 两次翻页各打一次接口，重看第一页与越界都不再出网
    assert [(c[1], c[3]) for c in fetcher.calls] == [(0, True), (100, False)]


def test_store_page_size_change_and_refresh_reset(monkeypatch):
    fetcher = FakeFetcher({0: _page(["u1"], 100, 1, min_time=100), 100: _page(["u2"], 0, 0)})
    monkeypatch.setattr(df, "fetch_following_page", fetcher)
    store = df.FollowingStore()

    async def scenario():
        await store.following_page("cookie", None, slot, page=1, page_size=2)
        await store.following_page("cookie", None, slot, page=1, page_size=50)  # 页大小变了 → 重置
        await store.following_page("cookie", None, slot, page=1, page_size=50, refresh=True)  # 手动刷新

    run(scenario())
    # 接口页只有 1 人、逻辑页要 2 人，所以每次请求都得顺游标取 2 个接口页；refresh 又重置一次
    assert [c[2] for c in fetcher.calls] == [2, 2, 50, 50, 50, 50]
    assert [c[3] for c in fetcher.calls] == [True, False, True, False, True, False]  # 每次重置后首发都是握手
    assert [c[1] for c in fetcher.calls] == [0, 100, 0, 100, 0, 100]


def test_store_merges_api_pages_into_logical_page(monkeypatch):
    """接口固定 ~20 条/次且不看 count，逻辑页由本地拼接：page_size=4 = 2 个接口页。"""
    fetcher = FakeFetcher(
        {
            0: _page(["u1", "u2"], 0, 1, min_time=10),
            10: _page(["u3", "u4"], 0, 1, min_time=20),
            20: _page(["u5", "u6"], 0, 1, min_time=30),
            30: _page(["u7"], 0, 0, min_time=40),
        }
    )
    monkeypatch.setattr(df, "fetch_following_page", fetcher)
    store = df.FollowingStore()

    async def scenario():
        first = await store.following_page("cookie", None, slot, page=1, page_size=4)
        second = await store.following_page("cookie", None, slot, page=2, page_size=4)
        return first, second

    first, second = run(scenario())
    assert [u["sec_uid"] for u in first["users"]] == ["u1", "u2", "u3", "u4"]
    assert first["max_page"] == 1 and first["has_more"] is True
    assert [u["sec_uid"] for u in second["users"]] == ["u5", "u6", "u7"]
    assert second["max_page"] == 2 and second["has_more"] is False  # 已到列表末尾
    assert len(fetcher.calls) == 4  # 第 1 页 2 次 + 第 2 页 2 次，没有重复请求


def test_store_cookie_change_resets_cache(monkeypatch):
    fetcher = FakeFetcher({0: _page(["u1"], 0, 0, min_time=0)})
    monkeypatch.setattr(df, "fetch_following_page", fetcher)
    store = df.FollowingStore()

    async def scenario():
        await store.following_page("cookie-a", None, slot, page=1, page_size=2)
        await store.following_page("cookie-b", None, slot, page=1, page_size=2)

    run(scenario())
    assert len(fetcher.calls) == 2  # 换 cookie 不串数据
    assert store._fingerprint  # 内存里只留指纹
    assert "cookie-a" not in store._fingerprint


def test_store_stops_when_cursor_missing(monkeypatch):
    """有 has_more 但响应没给 min_time 时必须停下，否则游标 0 会原地死循环。"""
    fetcher = FakeFetcher({0: _page(["u1"], 1699999999, 1)})  # min_time 缺省 0
    monkeypatch.setattr(df, "fetch_following_page", fetcher)
    store = df.FollowingStore()

    async def scenario():
        first = await store.following_page("cookie", None, slot, page=1, page_size=2)
        fifth = await store.following_page("cookie", None, slot, page=5, page_size=2)
        return first, fifth

    first, fifth = run(scenario())
    assert len(fetcher.calls) == 1  # 只出网一次
    assert [u["sec_uid"] for u in first["users"]] == ["u1"]
    assert [u["sec_uid"] for u in fifth["users"]] == ["u1"]  # 越界回落到最后一页
    assert fifth["page"] == 1  # 页码回真实值（请求的 5 并不存在）
    assert first["has_more"] is False


def test_store_walk_limit_bounds_single_request(monkeypatch):
    """手改 page=999 时不能在一个请求里把游标链翻到底（风控预算 + 请求耗时）。"""

    class InfiniteFetcher:
        def __init__(self):
            self.calls = 0

        async def __call__(self, cookie, proxy, *, max_time, page_size, first=False, sec_uid=None, uid=None):
            self.calls += 1
            return df.normalize_following_page(_page([f"u{self.calls}"], 0, 1, min_time=self.calls * 10))

    fetcher = InfiniteFetcher()
    monkeypatch.setattr(df, "fetch_following_page", fetcher)
    store = df.FollowingStore()

    async def scenario():
        return await store.following_page("cookie", None, slot, page=999, page_size=20)

    result = run(scenario())
    assert fetcher.calls == df.FOLLOWING_WALK_MAX  # 单次请求封顶
    # 凑不满逻辑页就回落到最后一个已取满的逻辑页（20 人/页 → 30 人 = 2 页，第 2 页只 10 人）
    assert result["page"] == 2
    assert result["max_page"] == 2
    assert result["has_more"] is True  # 没走到底，前端还能继续下一页
    assert [u["sec_uid"] for u in result["users"]] == [f"u{i}" for i in range(21, 31)]


def test_store_ttl_expiry_refetches(monkeypatch):
    fetcher = FakeFetcher({0: _page(["u1"], 0, 0, min_time=0)})
    monkeypatch.setattr(df, "fetch_following_page", fetcher)
    store = df.FollowingStore()

    async def scenario():
        await store.following_page("cookie", None, slot, page=1, page_size=2)
        store.loaded_at -= store.ttl + 1  # 模拟缓存过期
        await store.following_page("cookie", None, slot, page=1, page_size=2)

    run(scenario())
    assert len(fetcher.calls) == 2


def test_store_live_cache_and_refresh(monkeypatch):
    calls = []

    async def fake_live(cookie, proxy):
        calls.append(cookie)
        return [{"web_rid": "1", "anchor": {"nickname": "A"}}]

    monkeypatch.setattr(df, "fetch_live_rooms", fake_live)
    store = df.FollowingStore()

    async def scenario():
        first = await store.live("cookie", None, slot)
        second = await store.live("cookie", None, slot)
        third = await store.live("cookie", None, slot, refresh=True)
        return first, second, third

    first, second, third = run(scenario())
    assert len(calls) == 2  # 第二次命中 60s 缓存，refresh 才再打
    assert first["rooms"] == second["rooms"] == third["rooms"]
    assert first["fetched_at"] > 0


def test_store_self_profile_cached(monkeypatch):
    calls = []

    async def fake_profile(cookie, proxy):
        calls.append(cookie)
        return {"sec_uid": "s1", "nickname": "我"}

    monkeypatch.setattr(df, "fetch_self_profile", fake_profile)
    store = df.FollowingStore()

    async def scenario():
        await store.self_profile("cookie", None, slot)
        await store.self_profile("cookie", None, slot)

    run(scenario())
    assert len(calls) == 1


def test_store_pages_with_self_identity(monkeypatch):
    """翻页前先确保拿到自己的 sec_uid/uid（缓存命中时不多打一次资料接口）。"""
    profile_calls = []
    page_calls = []

    async def fake_profile(cookie, proxy):
        profile_calls.append(cookie)
        return {"sec_uid": "SELF", "uid": "123", "nickname": "我"}

    async def fake_page(cookie, proxy, **kwargs):
        page_calls.append(kwargs)
        return df.normalize_following_page(_page(["u1"], 0, 0))

    monkeypatch.setattr(df, "fetch_self_profile", fake_profile)
    monkeypatch.setattr(df, "fetch_following_page", fake_page)
    store = df.FollowingStore()

    async def scenario():
        await store.following_page("cookie", None, slot, page=1, page_size=2)
        store.loaded_at -= store.ttl + 1  # 缓存过期重新翻页
        await store.following_page("cookie", None, slot, page=1, page_size=2)

    run(scenario())
    assert [c["sec_uid"] for c in page_calls] == ["SELF", "SELF"]
    assert [c["uid"] for c in page_calls] == ["123", "123"]
    assert len(profile_calls) == 1  # 资料缓存 1h，只打一次

# ---------------------------------------------------------------- 播放直链


def _stream_data(**kwargs):
    """用 streamget 真实的 StreamData 构造，守住「字段名跟库对得上」这条线。"""
    from streamget.data import wrap_stream

    payload = {
        "platform": "抖音",
        "anchor_name": "小猫",
        "is_live": True,
        "title": "随便播播",
        "quality": "OD",
        "flv_url": "",
        "m3u8_url": "",
        "record_url": "",
        "live_url": "https://live.douyin.com/646634877123",
    }
    payload.update(kwargs)
    return wrap_stream(payload)


FLV_URL = (
    "http://pull-hs-f5.flive.douyincdn.com/stage/stream-6967_or4.flv"
    "?arch_hrchy=1&codec=h264&expire=1793161325&sign=abc&t_id=037-x&unique_id=s_68&volcSecret=y&volcTime=1793161325"
)
HLS_URL = "http://pull-hs-f5.flive.douyincdn.com/stage/stream-6967_or4/index.m3u8?codec=h265&expire=1793161325&sign=abc"


def test_normalize_stream_info_prefers_flv_and_upgrades_https():
    info = df.normalize_stream_info("646634877123", _stream_data(flv_url=FLV_URL, m3u8_url=HLS_URL))
    assert info["web_rid"] == "646634877123"
    assert info["is_live"] is True
    assert info["playback"] == "flv"
    # 直链是 http，但 CDN 两种协议都伺候；统一升 https，反代部署下才不会被 mixed content 拦
    assert info["play_url"].startswith("https://pull-hs-f5.flive.douyincdn.com/stage/")
    assert info["codec"] == "h264"
    assert info["quality"] == "OD"
    assert info["anchor_name"] == "小猫"
    assert info["expire_at"] == 1793161325


def test_normalize_stream_info_h265_falls_back_to_hls():
    """h265 的 flv 容器浏览器解不了（mpegts.js 只解 h264/aac），必须退回 hls。"""
    info = df.normalize_stream_info("1", _stream_data(flv_url=FLV_URL.replace("h264", "h265"), m3u8_url=HLS_URL))
    assert info["codec"] == "h265"
    assert info["playback"] == "hls"
    assert info["play_url"].endswith("/index.m3u8?codec=h265&expire=1793161325&sign=abc")


def test_normalize_stream_info_without_urls_when_offline():
    """没开播时 streamget 只给 is_live=False，没有任何地址。"""
    info = df.normalize_stream_info("1", _stream_data(is_live=False))
    assert info["is_live"] is False
    assert info["play_url"] == ""
    assert info["playback"] == ""
    assert info["expire_at"] == 0


def test_fetch_live_stream_url_maps_upstream_errors(monkeypatch):
    """风控页会让 streamget 抛 JSONDecodeError 之类，统一折成 upstream_error。"""

    class FakeLiveStream:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def fetch_web_stream_data(self, url):
            raise ValueError("当前服务繁忙")

    monkeypatch.setattr(df.streamget, "DouyinLiveStream", FakeLiveStream)
    with pytest.raises(df.DouyinFollowingError) as exc:
        run(df.fetch_live_stream_url("cookie", None, "1"))
    assert exc.value.code == "upstream_error"
    assert "ValueError" in exc.value.message


def test_fetch_live_stream_url_passes_room_url_and_quality(monkeypatch):
    seen = {}

    class FakeLiveStream:
        def __init__(self, proxy_addr=None, cookies=None):
            seen["proxy"] = proxy_addr
            seen["cookies"] = cookies

        async def fetch_web_stream_data(self, url):
            seen["url"] = url
            return {"fake": True}

        async def fetch_stream_url(self, payload, quality):
            seen["payload"] = payload
            seen["quality"] = quality
            return _stream_data(flv_url=FLV_URL)

    monkeypatch.setattr(df.streamget, "DouyinLiveStream", FakeLiveStream)
    info = run(df.fetch_live_stream_url("c", "http://127.0.0.1:7890", "4336413", "OD"))
    assert seen["url"] == "https://live.douyin.com/4336413"
    assert seen["quality"] == "OD"
    assert seen["proxy"] == "http://127.0.0.1:7890"
    assert seen["cookies"] == "c"
    assert info["playback"] == "flv"


def test_store_stream_caches_and_refresh_forces_refetch(monkeypatch):
    calls = []

    async def fake_stream(cookie, proxy, web_rid, quality=None):
        calls.append((web_rid, quality))
        return {"web_rid": web_rid, "is_live": True, "play_url": "https://x/1.flv", "playback": "flv"}

    monkeypatch.setattr(df, "fetch_live_stream_url", fake_stream)
    store = df.FollowingStore()

    async def scenario():
        first = await store.stream("cookie", None, slot, web_rid=" 646634877123 ")
        second = await store.stream("cookie", None, slot, web_rid="646634877123")
        third = await store.stream("cookie", None, slot, web_rid="646634877123", refresh=True)
        return first, second, third

    first, second, third = run(scenario())
    assert len(calls) == 2  # 命中的那次不再打接口
    assert calls[0][0] == "646634877123"  # web_rid 两侧空白被 strip
    assert first == second == third


def test_store_stream_cache_isolated_per_cookie_and_bounded(monkeypatch):
    calls = []

    async def fake_stream(cookie, proxy, web_rid, quality=None):
        calls.append(cookie)
        return {"web_rid": web_rid, "is_live": True, "play_url": "https://x/1.flv", "playback": "flv"}

    monkeypatch.setattr(df, "fetch_live_stream_url", fake_stream)
    store = df.FollowingStore()

    async def scenario():
        await store.stream("cookie-a", None, slot, web_rid="1")
        await store.stream("cookie-b", None, slot, web_rid="1")  # 换 Cookie 视为别人，重取
        for i in range(df.STREAM_CACHE_MAX + 1):
            await store.stream("cookie-b", None, slot, web_rid=str(100 + i))

    run(scenario())
    assert len(calls) == 2 + df.STREAM_CACHE_MAX + 1
    assert len(store.streams) <= df.STREAM_CACHE_MAX


def test_router_stream_endpoint_requires_sane_web_rid():
    """web_rid 会拼进 https://live.douyin.com/{web_rid}，必须挡住路径/查询注入。"""
    import re

    pattern = douyin_router._WEB_RID_PATTERN
    assert re.match(pattern, "646634877123")
    assert re.match(pattern, "MS4wLjABAAAA-x_1")
    assert not re.match(pattern, "a/b")
    assert not re.match(pattern, "a?x=1")
    assert not re.match(pattern, "")
    # 线上真实用到的两种 id 都要能被 Query(..., pattern=...) 放行
    assert re.fullmatch(pattern, "4336413")


def test_store_stream_cache_key_includes_quality(monkeypatch):
    """换清晰度必须重取：地址是按档位签的，不能把 OD 的直链当 HD1 用。"""
    calls = []

    async def fake_stream(cookie, proxy, web_rid, quality=None):
        calls.append(quality)
        return {"web_rid": web_rid, "is_live": True, "play_url": f"https://x/{quality or 'OD'}.flv"}

    monkeypatch.setattr(df, "fetch_live_stream_url", fake_stream)
    store = df.FollowingStore()

    async def scenario():
        od = await store.stream("cookie", None, slot, web_rid="1")
        od_again = await store.stream("cookie", None, slot, web_rid="1")
        hd = await store.stream("cookie", None, slot, web_rid="1", quality="HD1")
        return od, od_again, hd

    od, od_again, hd = run(scenario())
    assert calls == [None, "HD1"]
    assert od == od_again
    assert hd["play_url"] != od["play_url"]


# ---------------------------------------------------------------- Cookie 寿命 / 到期提醒

ISSUED = 1_792_553_337  # 2026-10-21 11:28:57 +08（生产那份 sid_guard 的签发时刻）
TTL = 60 * 24 * 3600  # 抖音网页登录态实测 60 天


def guard_cookie(issued=ISSUED, ttl=TTL, prefix="sessionid=secret; "):
    return prefix + f"sid_guard=secret|{issued}|{ttl}|Sun, 20 Dec 2026 03:28:57 GMT; ttwid=x"


def test_cookie_value_lookup_is_exact_name_match():
    cookie = "xysid_guard=1; sessionid_ss=y; sid_guard=a=b;  ttwid=z"
    assert df.parse_cookie_value(cookie, "sid_guard") == "a=b"  # 值里的 = 不能被切掉
    assert df.parse_cookie_value(cookie, "sessionid") == ""  # 不能前缀匹配到 sessionid_ss
    assert df.parse_cookie_value("", "sid_guard") == ""
    assert df.parse_cookie_value("sid_guard", "sid_guard") == ""  # 没等号的不是键值对
    assert df.parse_cookie_value(cookie, "ttwid") == "z"  # 前导空格不影响


@pytest.mark.parametrize("cookie", ["", "   ", None, "ttwid=x; msToken=y"])
def test_cookie_expiry_without_login_cookie_is_missing_or_unknown(cookie):
    payload = df.parse_cookie_expiry(cookie)
    assert payload["has_cookie"] is bool((cookie or "").strip())
    assert payload["state"] == ("unknown" if (cookie or "").strip() else "missing")
    assert payload["expires_at"] is None and payload["days_left"] is None


@pytest.mark.parametrize(
    "guard",
    [
        "sid_guard=",  # 空值
        "sid_guard=abc",  # 只有一段
        "sid_guard=abc|123",  # 只有两段
        "sid_guard=abc|notanumber|5184000|x",  # 时间戳不是数字
        "sid_guard=abc|1792553337|notanumber|x",  # 寿命不是数字
        "sid_guard=abc|1792553337|0|x",  # 寿命为 0
        "sid_guard=abc|1792553337|-100|x",  # 寿命为负
    ],
)
def test_cookie_expiry_with_unparsable_guard_is_unknown(guard):
    payload = df.parse_cookie_expiry(f"sessionid=secret; {guard}")
    assert payload["has_cookie"] is True
    assert payload["state"] == "unknown"
    assert payload["expires_at"] is None


def test_cookie_expiry_reports_ok_with_days_left():
    payload = df.parse_cookie_expiry(guard_cookie(), now=ISSUED)
    assert payload["state"] == "ok"
    assert payload["expires_at"] == ISSUED + TTL
    assert payload["issued_at"] == ISSUED
    assert payload["ttl_seconds"] == TTL
    assert payload["days_left"] == 60.0


def test_cookie_expiry_warns_only_inside_the_warn_window():
    """提醒窗口是「还剩 ≤ 7 天」：早一天不吵人，窗口内必须提醒。"""
    warn = df.COOKIE_EXPIRY_WARN_DAYS
    inside = df.parse_cookie_expiry(guard_cookie(), now=ISSUED + TTL - warn * 86400)
    assert inside["state"] == "expiring" and inside["days_left"] == 7.0

    outside = df.parse_cookie_expiry(guard_cookie(), now=ISSUED + TTL - (warn + 1) * 86400)
    assert outside["state"] == "ok"


def test_cookie_expiry_reports_expired():
    just_past = df.parse_cookie_expiry(guard_cookie(), now=ISSUED + TTL + 1)
    assert just_past["state"] == "expired"
    assert just_past["days_left"] == 0.0  # round(-1e-5, 1) 归一成 0.0，不是 -0.0

    long_past = df.parse_cookie_expiry(guard_cookie(), now=ISSUED + TTL + 10 * 86400)
    assert long_past["state"] == "expired"
    assert long_past["days_left"] == -10.0


def test_cookie_status_endpoint_works_without_cookie():
    """没 Cookie 时这个接口必须照样能用——它本来就是给「该提醒续期」的场景用的。"""

    class FakeSettings:
        user_config = {}

        def __init__(self, cookie):
            self._cookie = cookie

        def get_cookies_value(self, key):
            return self._cookie

    class Services:
        def __init__(self, cookie):
            self.settings_config = FakeSettings(cookie)

    assert run(douyin_router.get_cookie_status(user="admin", services=Services("")))["state"] == "missing"
    assert run(douyin_router.get_cookie_status(user="admin", services=Services("  ")))["state"] == "missing"


def test_cookie_status_endpoint_reports_expiry(monkeypatch):
    now = int(time.time())

    class FakeSettings:
        user_config = {}

        def __init__(self, cookie):
            self._cookie = cookie

        def get_cookies_value(self, key):
            return self._cookie

    class Services:
        def __init__(self, cookie):
            self.settings_config = FakeSettings(cookie)

    fresh = run(douyin_router.get_cookie_status(user="admin", services=Services(guard_cookie(issued=now))))
    assert fresh["state"] == "ok" and fresh["expires_at"] == now + TTL

    stale = run(
        douyin_router.get_cookie_status(
            user="admin", services=Services(guard_cookie(issued=now - 100 * 86400, ttl=60 * 86400))
        )
    )
    assert stale["state"] == "expired"


def test_cookie_expiry_handles_url_encoded_guard():
    """浏览器/插件复制出来的 cookie 串常把 | 写成 %7C——生产那份就是。

    只认裸 | 的话，真实 Cookie 会被判成 unknown，提醒功能等于没有。
    """
    encoded = (
        "sessionid=secret; sid_guard="
        "abc%7C1792553337%7C5184000%7CSun%2C+20+Dec+2026+03%3A28%3A57+GMT; ttwid=x"
    )
    payload = df.parse_cookie_expiry(encoded, now=ISSUED)
    assert payload["state"] == "ok"
    assert payload["expires_at"] == ISSUED + TTL
    assert payload["days_left"] == 60.0

    # 裸 | 和 %7C 混着写也一样认
    mixed = f"sid_guard=abc%7C{ISSUED}|{TTL}|Sun, 20 Dec 2026 03:28:57 GMT"
    assert df.parse_cookie_expiry(mixed, now=ISSUED)["state"] == "ok"
