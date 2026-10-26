"""抖音「推荐直播」：网页首页那个「换一批」的直播发现流。

接口是 ``live.douyin.com/webcast/feed/``，只需一条 ``ttwid`` 就能读（不需要 a_bogus、
不需要 sessionid），但**完全没有 Cookie 会被直接拒答（HTTP 444 空响应）**，所以匿名时
现取一条 ttwid（GET ``https://live.douyin.com/`` 的 Set-Cookie，内存缓存几小时）。

带登录 Cookie 时是强个性化：实测同一账号连拉两次重叠 0/20、每批全新，且与匿名热门榜
完全不同的池；Cookie 失效不会报错，平台自己退回热门榜（``extra.user_extra_info`` 落回
匿名那份 ``-5 / -1`` 哨兵值），所以这里遇到 ``login_required`` 也照样降级成匿名再试一次。

和关注列表一样：出网请求由调用方套平台节奏门（与周期监控共享预算），结果按 Cookie 指纹
做短时缓存，「换一批」用 ``refresh=True`` 真打接口。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

import httpx

from ...utils.logger import logger
from .douyin_following import (
    LOGIN_STATUS_CODES,
    PC_UA,
    REQUEST_TIMEOUT,
    DouyinFollowingError,
    _check_payload,
    _first_url,
    _https,
    _int,
)

RECOMMEND_FEED_URL = "https://live.douyin.com/webcast/feed/"
# 取匿名 ttwid 的入口：首页响应会带 Set-Cookie: ttwid=...
LIVE_HOME_URL = "https://live.douyin.com/"

# 网页版固定带的一组参数；实测最小参数集同样可用，这里留 serve 端认的这几个。
# 注意 source_key 必须是 web_homepage_hot_web_live_card：写 drawer_hot_live 之类
# 会 status_code=0 但 data 为空（静默失败，很容易误判成「要登录」）。
RECOMMEND_FEED_PARAMS: dict[str, str] = {
    "aid": "6383",
    "app_name": "douyin_web",
    "live_id": "1",
    "device_platform": "web",
    "language": "zh-CN",
    "channel": "channel_pc_web",
    "request_tag_from": "web",
    "need_map": "1",
    "liveid": "1",
    "is_draw": "1",
    "enter_source": "web_homepage_hot_web_live_card",
    "source_key": "web_homepage_hot_web_live_card",
    "is_ssr": "true",
    "action": "load_more",
    "action_type": "loadmore",
}

RECOMMEND_STORE_TTL = 60  # 与关注在播列表一致：一分钟内重复打开不重复打接口
TTWID_TTL = 6 * 60 * 60
# 这个接口一页固定 20 条（count/custom_count 都被忽略），且没有真分页：
# extra.max_time 是服务器当前毫秒时间戳，回传只会得到重叠数据，所以只能「换一批」。

_HEADERS = {
    "user-agent": PC_UA,
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-CN,zh;q=0.9",
    "referer": "https://live.douyin.com/",
}


def _is_personalized(payload: dict, sent_cookie: bool) -> bool:
    """判定这批到底是个性化还是热门榜。

    ``extra.user_extra_info`` 是个可靠的旁证：个性化请求带 ``user_feat``（``activeness``
    之类画像标签），匿名/登录态失效时只有 ``revenue_user_label: -5`` / ``life_layer_2: -1``
    这两个哨兵值。读不到就退回「我们有没有送登录 Cookie」这个朴素判据。
    """
    if not sent_cookie:
        return False
    extra = payload.get("extra")
    info = extra.get("user_extra_info") if isinstance(extra, dict) else None
    if isinstance(info, str):
        try:
            info = json.loads(info)
        except ValueError:
            info = None
    if not isinstance(info, dict):
        return True
    if _int(info.get("life_layer_2")) == -1 and _int(info.get("revenue_user_label")) == -5:
        return False
    return True


def normalize_recommend_rooms(payload: dict) -> list[dict] | None:
    """``webcast/feed`` 响应 → 推荐直播间列表；结构不符返回 None。

    和在播关注列表（``normalize_live_rooms``）输出同一种房间结构，前端能共用卡片与播放器。
    这一批里 ``data[].web_rid`` 就是 ``live.douyin.com/{web_rid}`` 短号（实测 20/20 都有），
    没有短号的条目直接丢掉——那些是平台塞的占位/已下播项。
    """
    if not isinstance(payload, dict):
        return None
    items = payload.get("data")
    if not isinstance(items, list):
        return None

    rooms: list[dict] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or item.get("is_ad"):
            continue
        room = item.get("data") if isinstance(item.get("data"), dict) else {}
        owner = room.get("owner") if isinstance(room.get("owner"), dict) else {}
        web_rid = str(item.get("web_rid") or owner.get("web_rid") or "")
        if not web_rid or not all(ch.isalnum() for ch in web_rid) or web_rid in seen:
            continue
        # 这个接口里在播就是 status=0（同一直播间下播后不会再出现在推荐流）
        if _int(room.get("status")) not in (None, 0):
            continue
        seen.add(web_rid)
        stats = room.get("room_view_stats") if isinstance(room.get("room_view_stats"), dict) else {}
        online = stats.get("display_short") or room.get("user_count_str") or _int(room.get("user_count"))
        rooms.append(
            {
                "web_rid": web_rid,
                "room_id": str(room.get("id_str") or ""),
                "title": str(room.get("title") or ""),
                "cover": _https(_first_url(room.get("cover"))),
                "online": str(online).replace("w", "万") if online else None,
                "tag": str(room.get("content_tag") or "") or None,
                "status": _int(room.get("status")),
                "anchor": {
                    "sec_uid": str(owner.get("sec_uid") or ""),
                    "nickname": str(owner.get("nickname") or ""),
                    "avatar": _https(_first_url(owner.get("avatar_thumb"))),
                    "web_rid": str(owner.get("web_rid") or ""),
                },
            }
        )
    return rooms


# ---------------------------------------------------------------- 匿名 ttwid

_ttwid_lock = asyncio.Lock()
_ttwid_cache: tuple[float, str] | None = None


async def fetch_anonymous_ttwid(proxy: str | None = None) -> str:
    """现取一条匿名 ttwid（首页 Set-Cookie）。"""
    try:
        async with httpx.AsyncClient(proxy=proxy or None, timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(LIVE_HOME_URL, headers={"user-agent": PC_UA})
    except httpx.HTTPError as exc:
        raise DouyinFollowingError("upstream_error", f"取抖音直播页失败：{type(exc).__name__}: {exc}") from exc
    ttwid = response.cookies.get("ttwid")
    if not ttwid:
        raise DouyinFollowingError("bad_response", f"抖音直播页没有下发 ttwid（HTTP {response.status_code}）")
    return ttwid


async def _ensure_ttwid(proxy: str | None, force: bool = False) -> str:
    global _ttwid_cache
    async with _ttwid_lock:
        if not force and _ttwid_cache and time.time() - _ttwid_cache[0] < TTWID_TTL:
            return _ttwid_cache[1]
        ttwid = await fetch_anonymous_ttwid(proxy)
        _ttwid_cache = (time.time(), ttwid)
        logger.debug("Douyin live feed: 已刷新匿名 ttwid")
        return ttwid


def reset_anonymous_ttwid() -> None:
    """测试/换代理时丢掉缓存的 ttwid。"""
    global _ttwid_cache
    _ttwid_cache = None


# ---------------------------------------------------------------- HTTP


async def _get_feed(cookie: str, proxy: str | None) -> dict:
    headers = dict(_HEADERS)
    if cookie:
        headers["cookie"] = cookie
    try:
        async with httpx.AsyncClient(proxy=proxy or None, timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(RECOMMEND_FEED_URL, params=RECOMMEND_FEED_PARAMS, headers=headers)
    except httpx.HTTPError as exc:
        raise DouyinFollowingError("upstream_error", f"请求抖音推荐直播失败：{type(exc).__name__}: {exc}") from exc
    if response.status_code != 200:
        # 444 是字节的空响应拒答（常见于完全不带 Cookie）
        raise DouyinFollowingError("upstream_error", f"抖音推荐直播返回 HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise DouyinFollowingError("bad_response", "抖音推荐直播返回非 JSON（可能触发风控）") from exc
    return _check_payload(payload)


async def fetch_recommend_rooms(cookie: str, proxy: str | None) -> tuple[list[dict], bool]:
    """推荐直播间 + 是否个性化。

    带 Cookie 就个性化；Cookie 被判未登录时**不报错**，退回匿名热门榜（与用户实际
    在网页上看到的行为一致）。
    """
    cookie = (cookie or "").strip()
    if cookie:
        try:
            payload = await _get_feed(cookie, proxy)
            rooms = normalize_recommend_rooms(payload)
            if rooms is not None:
                return rooms, _is_personalized(payload, True)
            logger.warning("Douyin live feed: 登录态返回结构异常，改用热门榜")
        except DouyinFollowingError as exc:
            if exc.code != "login_required":
                raise
            logger.warning(f"Douyin live feed: 登录态失效（status={exc.status_code}），改用热门榜")

    for attempt in (1, 2):
        ttwid = await _ensure_ttwid(proxy, force=attempt == 2)
        try:
            payload = await _get_feed(f"ttwid={ttwid}", proxy)
        except DouyinFollowingError as exc:
            # 缓存的 ttwid 过期时平台也回 444/非 JSON，换一条再试一次
            if attempt == 1 and exc.code in ("bad_response", "upstream_error"):
                logger.warning(f"Douyin live feed: 匿名 ttwid 可能失效（{exc.message}），换一条重试")
                continue
            raise
        rooms = normalize_recommend_rooms(payload)
        if rooms is None:
            raise DouyinFollowingError("bad_response", "抖音推荐直播返回的结构无法解析")
        return rooms, False

    raise DouyinFollowingError("bad_response", "抖音推荐直播没有拿到可用的匿名 ttwid")


# ---------------------------------------------------------------- 缓存


class RecommendStore:
    """推荐流的内存缓存（按 Cookie 指纹隔离，内存里只留指纹）。"""

    def __init__(self, ttl: int = RECOMMEND_STORE_TTL):
        self.ttl = ttl
        self._lock = asyncio.Lock()
        self._fingerprint = ""
        self.rooms: list[dict] = []
        self.personalized = False
        self.fetched_at = 0.0

    def _bind(self, cookie: str) -> None:
        fingerprint = hashlib.sha1((cookie or "").encode("utf-8")).hexdigest()
        if fingerprint != self._fingerprint:
            self._fingerprint = fingerprint
            self.reset()

    def reset(self) -> None:
        self.rooms = []
        self.personalized = False
        self.fetched_at = 0.0

    async def feed(
        self, cookie: str, proxy: str | None, slot: Any, refresh: bool = False
    ) -> dict:
        async with self._lock:
            self._bind(cookie)
            fresh = self.fetched_at > 0 and time.time() - self.fetched_at < self.ttl
            if refresh or not fresh:
                async with slot():
                    self.rooms, self.personalized = await fetch_recommend_rooms(cookie, proxy)
                self.fetched_at = time.time()
                logger.debug(
                    f"Douyin live feed refreshed: {len(self.rooms)} rooms "
                    f"personalized={self.personalized}"
                )
            return {
                "rooms": list(self.rooms),
                "personalized": self.personalized,
                "fetched_at": self.fetched_at,
            }


_STORE = RecommendStore()


def get_store() -> RecommendStore:
    return _STORE


__all__ = [
    "LOGIN_STATUS_CODES",
    "RecommendStore",
    "fetch_anonymous_ttwid",
    "fetch_recommend_rooms",
    "get_store",
    "normalize_recommend_rooms",
    "reset_anonymous_ttwid",
]