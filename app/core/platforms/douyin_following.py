"""抖音关注列表：用账号 Cookie 走网页接口读「我关注的人」与「关注的人谁在直播」。

签名复用 streamget 的 a_bogus 实现（与录制、有效性检测同一套），Cookie 取设置页
里 douyin 平台那一份——必须含登录态（sessionid 等），只有 ttwid 的公共 Cookie
会被抖音判成「用户未登录」。

翻页按抖音网页接口的游标语义实现：第一页先用 ``source_type=2`` 握手拿 ``max_time``，
之后每页把上一页返回的 ``min_time`` 当 ``max_time`` 传（不是 ``max_time``，否则相邻页会重叠）；
结果按「游标链」缓存在内存里（翻回上一页不再打接口）。所有出网请求由调用方套平台
节奏门，与周期监控共享单位时间预算。

同一个模块还负责「在网页里看关注的人直播」要用的播放直链：走 streamget 的 webcast
room enter（与录制取流是同一条路径），把 flv/hls 地址交给前端播放器。注意地址是抖音
CDN 的签名直链（``expire``/``sign``/``volcSecret``），有寿命、且主播重开会换 stream id，
所以只做短时缓存，前端播放失败时重取。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
import urllib.parse
from typing import Any, Callable, ContextManager

import httpx
import streamget

from streamget.platforms.douyin.ab_sign import ab_sign

from ...utils.logger import logger

PC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

SELF_PROFILE_URL = "https://www.douyin.com/aweme/v1/web/user/profile/self/"
FOLLOWING_LIST_URL = "https://www.douyin.com/aweme/v1/web/user/following/list/"
FOLLOWING_LIVE_URL = "https://www.douyin.com/webcast/web/feed/follow/"

# 抖音网页端固定带的一组设备/环境参数；缺失时部分接口直接拒答。
WEB_DEVICE_PARAMS: dict[str, str] = {
    "device_platform": "webapp",
    "aid": "6383",
    "channel": "channel_pc_web",
    "pc_client_type": "1",
    "version_code": "290100",
    "version_name": "29.1.0",
    "cookie_enabled": "true",
    "screen_width": "1920",
    "screen_height": "1080",
    "browser_language": "zh-CN",
    "browser_platform": "Win32",
    "browser_name": "Chrome",
    "browser_version": "141.0.0.0",
    "browser_online": "true",
    "engine_name": "Blink",
    "engine_version": "141.0.0.0",
    "os_name": "Windows",
    "os_version": "10",
    "cpu_core_num": "16",
    "device_memory": "8",
    "platform": "PC",
    "downlink": "10",
    "effective_type": "4g",
    "round_trip_time": "50",
}

FOLLOWING_PAGE_MAX = 100
FOLLOWING_STORE_TTL = 10 * 60
# 单次请求最多往后翻几页：游标链必须顺序翻，page=999 这类手改参数否则会在一个请求里
# 打几十上百次接口（既拖住请求，也把平台节奏门的风控预算吃干净）。缓存是增量的，
# 前端点「下一页」时每请求只往前一页，不受这个上限影响。
FOLLOWING_WALK_MAX = 30
LIVE_STORE_TTL = 60
# 播放直链缓存：地址本身签名有效期很长（实测 expire 是 epoch，约 7 天），但主播重开播会
# 换 stream id，所以只压几十秒——同一个直播间反复打开不必每次都打接口。
STREAM_URL_TTL = 60
STREAM_CACHE_MAX = 64
SELF_PROFILE_TTL = 60 * 60
REQUEST_TIMEOUT = 15.0

# 登录态到期提醒：抖音的 sid_guard 里带签发时间与有效期（60 天），提前这么多天开始提示。
# 关注页是只读的，失效最多让人扫一次码；提前提醒是为了别等到录播/查列表时才发现。
COOKIE_EXPIRY_WARN_DAYS = 7

# 抖音「未登录」类业务码：8=用户未登录，20003=User doesn't login（直播域）
LOGIN_STATUS_CODES = {8, 20003, 20005}


class DouyinFollowingError(Exception):
    """关注列表功能的可分类错误；``code`` 由路由翻译成前端错误码。"""

    def __init__(self, code: str, message: str = "", status_code: int | None = None):
        super().__init__(message or code)
        self.code = code
        self.message = message
        self.status_code = status_code


# ---------------------------------------------------------------- 归一化（纯函数）


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


def _first_url(node: Any) -> str:
    if isinstance(node, dict):
        urls = node.get("url_list")
        if isinstance(urls, list):
            for item in urls:
                if isinstance(item, str) and item:
                    return item
    if isinstance(node, str):
        return node
    return ""


def _query_param(url: str, name: str) -> str:
    """取 URL query 里的某个参数（播放直链的 ``codec``/``expire`` 就藏在签名参数里）。"""
    if not url:
        return ""
    return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query)).get(name, "")


def _https(value: str) -> str:
    return value.replace("http://", "https://", 1) if value.startswith("http://") else value


def _expire_at(url: str) -> int:
    """直链签名里的 ``expire`` 是**十六进制**时间戳（``expire=6aed544d``），不是十进制。

    以前统一按十进制解析，生产上恒为 0；这里两种都认（纯数字串按十进制，其它按十六进制）。
    """
    raw = _query_param(url, "expire").strip()
    if not raw:
        return 0
    try:
        return int(raw) if raw.isdigit() else int(raw, 16)
    except ValueError:
        return 0


def parse_cookie_value(cookie: str, name: str) -> str:
    """从 Cookie 串里取一个字段的值（纯函数）；没有就返回空串。"""
    for part in (cookie or "").split(";"):
        key, sep, value = part.partition("=")
        if sep and key.strip() == name:
            return value.strip()
    return ""


def parse_cookie_expiry(cookie: str, now: float | None = None) -> dict[str, Any]:
    """解析 Cookie 里的登录态寿命（纯函数，不发请求）。

    抖音网页端唯一带寿命的登录字段是 ``sid_guard``，形如
    ``<sessionid>|<issue_ts>|<ttl_seconds>|<http-date>``。注意从浏览器/插件
    复制出来的 cookie 串常把 ``|`` 写成 ``%7C``（生产那份就是），所以先
    URL 解码再分段。解析不出来不能当错误：用户可能只填了半串、只填 ttwid、
    或者手动改过，这些情况一律报 ``unknown``。

    ``state``：missing 没配 / unknown 读不到寿命 / expired 已过期 /
    expiring 快到期（``COOKIE_EXPIRY_WARN_DAYS`` 天内）/ ok 还早。
    """
    cookie = (cookie or "").strip()
    if not cookie:
        return {
            "has_cookie": False,
            "state": "missing",
            "expires_at": None,
            "issued_at": None,
            "ttl_seconds": None,
            "days_left": None,
        }

    result: dict[str, Any] = {
        "has_cookie": True,
        "state": "unknown",
        "expires_at": None,
        "issued_at": None,
        "ttl_seconds": None,
        "days_left": None,
    }

    guard = urllib.parse.unquote(parse_cookie_value(cookie, "sid_guard"))
    parts = guard.split("|")
    issued = _int(parts[1]) if len(parts) >= 3 else None
    ttl = _int(parts[2]) if len(parts) >= 3 else None
    if not issued or not ttl or ttl <= 0:
        return result

    expires_at = issued + ttl
    days_left = (expires_at - (time.time() if now is None else float(now))) / 86400
    if days_left <= 0:
        state = "expired"
    elif days_left <= COOKIE_EXPIRY_WARN_DAYS:
        state = "expiring"
    else:
        state = "ok"

    result.update(
        state=state,
        expires_at=int(expires_at),
        issued_at=int(issued),
        ttl_seconds=int(ttl),
        # round(-1e-5, 1) 是 -0.0：归一成 0.0，免得前端显示「还剩 -0 天」
        days_left=round(days_left, 1) or 0.0,
    )
    return result


def normalize_self_profile(payload: dict) -> dict | None:
    """``user/profile/self`` 响应 → 当前账号精简信息；缺 sec_uid 视为无效。"""
    user = payload.get("user") if isinstance(payload, dict) else None
    if not isinstance(user, dict):
        return None
    sec_uid = user.get("sec_uid")
    if not isinstance(sec_uid, str) or not sec_uid:
        return None
    return {
        "sec_uid": sec_uid,
        "uid": str(user.get("uid") or ""),
        "nickname": str(user.get("nickname") or ""),
        "unique_id": str(user.get("unique_id") or user.get("short_id") or ""),
        "avatar": _https(_first_url(user.get("avatar_thumb")) or _first_url(user.get("avatar_168x168"))),
        "follower_count": _int(user.get("follower_count")),
        "following_count": _int(user.get("following_count")),
        "aweme_count": _int(user.get("aweme_count")),
    }


def _normalize_user(item: Any) -> dict | None:
    if not isinstance(item, dict):
        return None
    # 有的版本把用户对象套在 item.user 里，顶层字段优先、嵌套补缺
    nested = item.get("user")
    source: dict = {**nested, **item} if isinstance(nested, dict) else item
    sec_uid = source.get("sec_uid")
    if not isinstance(sec_uid, str) or not sec_uid:
        return None
    short_id = str(source.get("short_id") or "")
    unique_id = str(source.get("unique_id") or "")
    custom_verify = str(source.get("custom_verify") or "")
    return {
        "sec_uid": sec_uid,
        "uid": str(source.get("uid") or ""),
        "nickname": str(source.get("nickname") or "").strip(),
        "unique_id": unique_id or (short_id if short_id != "0" else ""),
        "short_id": "" if short_id == "0" else short_id,
        "signature": str(source.get("signature") or ""),
        "avatar": _https(_first_url(source.get("avatar_thumb")) or _first_url(source.get("avatar_168x168"))),
        "follower_count": _int(source.get("follower_count")),
        "following_count": _int(source.get("following_count")),
        "aweme_count": _int(source.get("aweme_count")),
        "verified": bool(source.get("is_verified")) or bool(custom_verify),
        "custom_verify": custom_verify,
        # 部分版本会在关注列表里直接带在播房间，用来给前端标「直播中」
        "room_id": str(source.get("room_id") or ""),
        "room_data": source.get("room_data") if isinstance(source.get("room_data"), dict) else None,
    }


def normalize_following_page(payload: dict) -> dict:
    """``user/following/list`` 响应 → {users, has_more, max_time, total}。"""
    payload = payload if isinstance(payload, dict) else {}
    raw = payload.get("followings")
    if not isinstance(raw, list):
        # 该接口的数组键历史上变过（follow_list 等）
        raw = payload.get("follow_list")
    users: list[dict] = []
    if isinstance(raw, list):
        for item in raw:
            user = _normalize_user(item)
            if user is not None:
                users.append(user)
    return {
        "users": users,
        "has_more": bool(payload.get("has_more")),
        "max_time": _int(payload.get("max_time")) or 0,
        # 下一页要传的游标（见 fetch_following_page 注释）
        "min_time": _int(payload.get("min_time")) or 0,
        "total": _int(payload.get("total")),
    }


# pull_data.stream_data 的档位名 → 抖音网页上的清晰度叫法
_PULL_QUALITY_LABELS = {
    "origin": "原画",
    "uhd": "蓝光",
    "hd": "超清",
    "sd": "标清",
    "ld": "流畅",
    "md": "移动",
}


def _pull_stream_map(stream_url: Any) -> dict[str, Any]:
    """``live_core_sdk_data.pull_data.stream_data`` 是个 **JSON 字符串**，拆出档位表。"""
    if not isinstance(stream_url, dict):
        return {}
    pull = stream_url.get("live_core_sdk_data")
    raw = ((pull.get("pull_data") or {}) if isinstance(pull, dict) else {}).get("stream_data")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    if not isinstance(raw, dict):
        return {}
    data = raw.get("data")
    return data if isinstance(data, dict) else {}


def extract_direct_play(stream_url: Any) -> dict:
    """从列表接口（关注在播）的 ``room.stream_url`` 里挑一条浏览器能直解的直链。

    结论来自 2026-10-30 生产实测：``webcast/web/feed/follow`` 返回的签名直链在播关注那批
    就是 **h264 全画质**（1088x1920，与 room enter 取的档次一致），可以直接拿来播，
    省掉一次 ``/live/stream``（一次后端请求 + 一到两次上游请求）。

    只认 ``pull_data`` 里 ``sdk_params.VCodec == h264`` 的档位：h265 的 flv 容器 mpegts.js
    解不了（桌面 Chromium 也没 HEVC 解码器）。legacy ``flv_pull_url``（FULL_HD1 那套）
    不带编码信息，不参与选择。

    **推荐直播（``webcast/feed``）不要用这个**：实测 20/20 房间主画质全是 h265，唯一的 h264
    档位是 240x423（``md``），而走 room enter 能拿到 1088x1920 的 h264，画质降级太狠。
    """
    best: dict | None = None
    best_bitrate = -1
    for key, node in _pull_stream_map(stream_url).items():
        main = node.get("main") if isinstance(node, dict) else None
        if not isinstance(main, dict):
            continue
        url = main.get("flv") or ""
        # ao 档是纯音频（only_audio=1），塞给 <video> 只会黑屏
        if not url or "only_audio=1" in url:
            continue
        try:
            params = json.loads(main.get("sdk_params") or "{}")
        except (TypeError, ValueError):
            params = {}
        if not isinstance(params, dict) or str(params.get("VCodec") or "").lower() != "h264":
            continue
        bitrate = _int(params.get("vbitrate")) or 0
        if bitrate <= best_bitrate:
            continue
        best_bitrate = bitrate
        best = {
            # 与 /live/stream 一致：统一升 https（反代部署下 http 直链会被 mixed content 拦）
            "play_url": _https(url),
            "playback": "flv",
            "quality": _PULL_QUALITY_LABELS.get(key) or str(params.get("resolution") or key),
            "codec": "h264",
            "expire_at": _expire_at(url),
        }
    return best or {"play_url": "", "playback": "", "quality": "", "codec": "", "expire_at": 0}


def normalize_live_rooms(payload: dict) -> list[dict] | None:
    """``webcast/web/feed/follow`` 响应 → 正在直播的关注用户；结构不符返回 None。"""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None

    rooms: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        room = item.get("room") if isinstance(item.get("room"), dict) else None
        if room is None:
            room = item.get("data") if isinstance(item.get("data"), dict) else {}
        owner = room.get("owner") if isinstance(room.get("owner"), dict) else {}
        web_rid = str(item.get("web_rid") or owner.get("web_rid") or "")
        if not web_rid or not all(ch.isalnum() for ch in web_rid):
            continue
        # 只放在播的人；下播的房间 status=4
        if _int(room.get("status")) == 4:
            continue
        stats = room.get("room_view_stats") if isinstance(room.get("room_view_stats"), dict) else {}
        online = stats.get("display_short") or room.get("user_count_str")
        rooms.append(
            {
                "web_rid": web_rid,
                "room_id": str(room.get("id_str") or item.get("room_id") or ""),
                "title": str(room.get("title") or ""),
                "cover": _https(_first_url(room.get("cover"))),
                "online": str(online).replace("w", "万") if online else None,
                "tag": str(item.get("tag_name") or "") or None,
                "status": _int(room.get("status")),
                "anchor": {
                    "sec_uid": str(owner.get("sec_uid") or ""),
                    "nickname": str(owner.get("nickname") or ""),
                    "avatar": _https(_first_url(owner.get("avatar_thumb"))),
                    "web_rid": str(owner.get("web_rid") or ""),
                },
                # 列表里自带的签名直链（h264 时可直接播，省一次 /live/stream）
                **extract_direct_play(room.get("stream_url")),
            }
        )
    return rooms


# ---------------------------------------------------------------- HTTP


def _build_signed_url(url: str, params: dict[str, Any]) -> tuple[str, dict[str, str]]:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    a_bogus = ab_sign(query, PC_UA)
    full = f"{url}?{query}&a_bogus={urllib.parse.quote(a_bogus, safe='')}"
    headers = {
        "user-agent": PC_UA,
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9",
        "referer": "https://www.douyin.com/",
    }
    return full, headers


def _check_payload(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise DouyinFollowingError("bad_response", "抖音接口返回的结构无法解析")
    status_code = _int(payload.get("status_code"))
    if status_code in LOGIN_STATUS_CODES:
        raise DouyinFollowingError(
            "login_required",
            "抖音 Cookie 登录态已失效，请在设置里重新填入带登录态的 Cookie",
            status_code=status_code,
        )
    if status_code not in (None, 0):
        message = str(payload.get("status_msg") or "")
        raise DouyinFollowingError(
            "upstream_error",
            f"抖音接口返回 status_code={status_code} {message}".strip(),
            status_code=status_code,
        )
    return payload


async def _request_json(url: str, params: dict[str, Any], cookie: str, proxy: str | None) -> dict:
    full, headers = _build_signed_url(url, params)
    headers["cookie"] = cookie
    try:
        async with httpx.AsyncClient(proxy=proxy or None, timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(full, headers=headers)
    except httpx.HTTPError as exc:
        raise DouyinFollowingError("upstream_error", f"请求抖音接口失败：{type(exc).__name__}: {exc}") from exc
    if response.status_code != 200:
        raise DouyinFollowingError("upstream_error", f"抖音接口返回 HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        # 风控页不解 JSON，落到这里
        raise DouyinFollowingError("bad_response", "抖音接口返回非 JSON（可能触发风控）") from exc
    return _check_payload(payload)


async def fetch_self_profile(cookie: str, proxy: str | None) -> dict:
    payload = await _request_json(SELF_PROFILE_URL, dict(WEB_DEVICE_PARAMS), cookie, proxy)
    profile = normalize_self_profile(payload)
    if profile is None:
        # 登录态没了时，抖音对匿名请求也可能回 status_code=0，只是 user 为 null
        # （DDr 的 verifyIdentity 同样按 user 是否为空判登录态）。这种情况必须报
        # login_required，前端才会引导去扫码，别报成「上游异常」。
        user = payload.get("user") if isinstance(payload, dict) else None
        if user is None:
            raise DouyinFollowingError(
                "login_required", "抖音返回的用户信息为空，Cookie 登录态多半已失效"
            )
        raise DouyinFollowingError("bad_response", "抖音接口没有返回当前账号信息")
    return profile


async def fetch_following_page(
    cookie: str,
    proxy: str | None,
    *,
    max_time: int,
    page_size: int,
    first: bool = False,
    sec_uid: str | None = None,
    uid: str | None = None,
) -> dict:
    """取一页关注用户。

    ``sec_uid``/``uid`` 是当前登录账号自己的（来自 ``user/profile/self``）：抖音这条
    接口要求带上被查对象的 id，只靠 Cookie 认人时会返回空。
    ``first`` 时先按抖音的握手约定（source_type=2）拿起始游标，握手响应本身带
    数据就直接用，不带（现在的行为）再拿 ``max_time`` 补一页，避免重复数据。

    翻页语义（2026-10-21 用真实账号全量翻完 22 页核对过）：请求参数 ``max_time`` 是
    「只看这个时间之前的关注」，响应里的 ``max_time`` / ``min_time`` 分别是本页时间窗
    的最新 / 最旧一条。**下一页要把上一页的 ``min_time`` 当 ``max_time`` 传**，传上一页的
    ``max_time`` 会退回同一窗口整体后移一位的重叠数据。续页统一用 ``source_type=1``。
    """
    count = max(1, min(int(page_size), FOLLOWING_PAGE_MAX))
    owner: dict[str, Any] = {"min_time": 0, "offset": 0, "address_book_access": 0, "gps_access": 0}
    if sec_uid:
        owner["sec_user_id"] = sec_uid
    if uid:
        owner["user_id"] = uid

    if first:
        handshake = await _request_json(
            FOLLOWING_LIST_URL,
            {**WEB_DEVICE_PARAMS, **owner, "max_time": 0, "count": count, "source_type": 2, "is_top": 1},
            cookie,
            proxy,
        )
        page = normalize_following_page(handshake)
        if page["users"]:
            return page
        cursor = page["max_time"]
        if not cursor:
            return page
        max_time = cursor

    payload = await _request_json(
        FOLLOWING_LIST_URL,
        {**WEB_DEVICE_PARAMS, **owner, "max_time": max_time, "count": count, "source_type": 1, "is_top": 1},
        cookie,
        proxy,
    )
    return normalize_following_page(payload)


async def fetch_live_rooms(cookie: str, proxy: str | None) -> list[dict]:
    payload = await _request_json(
        FOLLOWING_LIVE_URL,
        {
            "aid": "6383",
            "device_platform": "webapp",
            "channel": "channel_pc_web",
            "request_tag_from": "web",
            "scene": "aweme_pc_follow_top",
        },
        cookie,
        proxy,
    )
    rooms = normalize_live_rooms(payload)
    if rooms is None:
        raise DouyinFollowingError("bad_response", "抖音接口没有返回直播间数据")
    return rooms


# ---------------------------------------------------------------- 缓存 / 翻页编排

SlotFactory = Callable[[], ContextManager]


def normalize_stream_info(web_rid: str, info: Any) -> dict:
    """把 streamget 的 ``StreamData`` 折算成播放器要的字段（纯函数）。"""
    # 直链两种协议 CDN 都伺候（实测同一 host 换 https 一样 200 video/x-flv），统一升成
    # https：反代/HTTPS 部署下页面里放 http 直链会被浏览器按 mixed content 拦掉。
    flv_url = _https(getattr(info, "flv_url", "") or "")
    hls_url = _https(getattr(info, "m3u8_url", "") or "")
    source = flv_url or hls_url
    codec = _query_param(source, "codec")
    # h265 的 flv 容器浏览器解不了（mpegts.js 只解 h264/aac），退回 hls
    if codec == "h265" and hls_url:
        playback = "hls"
    elif flv_url:
        playback = "flv"
    else:
        playback = "hls" if hls_url else ""
    return {
        "web_rid": str(web_rid),
        "is_live": bool(getattr(info, "is_live", False)),
        "play_url": flv_url if playback == "flv" else (hls_url if playback == "hls" else ""),
        "playback": playback,
        "quality": getattr(info, "quality", "") or "",
        "codec": codec,
        "title": getattr(info, "title", "") or "",
        "anchor_name": getattr(info, "anchor_name", "") or "",
        "expire_at": _expire_at(source),
    }


async def fetch_live_stream_url(
    cookie: str, proxy: str | None, web_rid: str, quality: str | None = None
) -> dict:
    """取某个直播间的实时播放直链（录制取流走的就是这条 streamget 路径）。

    Cookie 可选：room enter 是游客接口（录制不带登录态也能取流）。风控页会让 streamget
    抛 JSONDecodeError 之类的意外异常，统一折成 ``upstream_error``。
    """
    live_stream = streamget.DouyinLiveStream(proxy_addr=proxy or None, cookies=cookie or None)
    try:
        payload = await live_stream.fetch_web_stream_data(url=f"https://live.douyin.com/{web_rid}")
        info = await live_stream.fetch_stream_url(payload, quality)
    except Exception as exc:
        raise DouyinFollowingError("upstream_error", f"{type(exc).__name__}: {exc}") from exc
    return normalize_stream_info(web_rid, info)


class FollowingStore:
    """关注列表 + 在播列表的内存缓存（按 Cookie 指纹隔离，内存里只留指纹）。

    关注列表是「游标链」：``pages[i]`` 是取第 i 次接口请求拿到的原始页，``cursors[i]`` 是
    取该页时用的 ``max_time``，取到后把它的 ``min_time`` 追加为下一段游标。客户端翻到
    已缓存的页直接命中，翻新页才出网。

    抖音自己的分页固定 ~20 条/次且不看我们传的 ``count``（实测 ``count=100`` 仍只返回
    20 条），所以「逻辑页」由本地拼接：``page_size=100`` 就是拼 5 个接口页。
    """

    def __init__(self, ttl: int = FOLLOWING_STORE_TTL, live_ttl: int = LIVE_STORE_TTL):
        self.ttl = ttl
        self.live_ttl = live_ttl
        self._lock = asyncio.Lock()
        self._fingerprint = ""
        self.page_size = 0
        self.profile: dict | None = None
        self.profile_at = 0.0
        self.pages: list[dict] = []
        self.cursors: list[int] = [0]
        self.exhausted = False
        self.loaded_at = 0.0
        self.live_rooms: list[dict] | None = None
        self.live_at = 0.0
        # 键是 (web_rid, quality)：换档位不能拿上一档的地址
        self.streams: dict[tuple[str, str], tuple[float, dict]] = {}

    # -- 内部

    def _bind(self, cookie: str) -> None:
        fingerprint = hashlib.sha1(cookie.encode("utf-8")).hexdigest()
        if fingerprint != self._fingerprint:
            self._fingerprint = fingerprint
            self.reset()

    def reset(self) -> None:
        self.page_size = 0
        self.pages = []
        self.cursors = [0]
        self.exhausted = False
        self.loaded_at = 0.0
        self.streams = {}

    async def _ensure_profile(
        self, cookie: str, proxy: str | None, slot: SlotFactory, refresh: bool = False
    ) -> dict:
        """确保拿到当前账号资料（调用方必须已持有 ``_lock``）。"""
        fresh = self.profile is not None and time.time() - self.profile_at < SELF_PROFILE_TTL
        if refresh or not fresh:
            async with slot():
                self.profile = await fetch_self_profile(cookie, proxy)
            self.profile_at = time.time()
        return dict(self.profile or {})

    def _loaded_users(self) -> list[dict]:
        """已取到的关注用户，按游标链顺序拼平。"""
        return [user for page in self.pages for user in page["users"]]

    async def _load_page(self, cookie: str, proxy: str | None, slot: SlotFactory) -> None:
        first = not self.pages
        cursor = self.cursors[-1]
        # 关注列表接口要带自己的 sec_user_id / uid（原来只有 Cookie 时返回空列表）
        profile = await self._ensure_profile(cookie, proxy, slot)
        async with slot():
            page = await fetch_following_page(
                cookie,
                proxy,
                max_time=cursor,
                page_size=self.page_size,
                first=first,
                sec_uid=profile.get("sec_uid") or None,
                uid=profile.get("uid") or None,
            )
        self.pages.append(page)
        # 实测（2026-10-21，一个 429 关注的账号全量翻完）：响应里的 max_time / min_time 是
        # 本页时间窗的上下界，下一页要传的游标是 min_time；把上一页的 max_time 原样传回去
        # 只会得到同一窗口整体后移一位的重叠数据（页码越翻越糊）。
        cursor = page["min_time"]
        self.cursors.append(cursor)
        self.loaded_at = time.time()
        if not page["has_more"] or not page["users"] or not cursor:
            self.exhausted = True

    # -- 对外

    async def self_profile(self, cookie: str, proxy: str | None, slot: SlotFactory, refresh: bool = False) -> dict:
        async with self._lock:
            self._bind(cookie)
            return await self._ensure_profile(cookie, proxy, slot, refresh)

    async def following_page(
        self,
        cookie: str,
        proxy: str | None,
        slot: SlotFactory,
        page: int = 1,
        page_size: int = 20,
        refresh: bool = False,
    ) -> dict:
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), FOLLOWING_PAGE_MAX))
        async with self._lock:
            self._bind(cookie)
            if refresh or page_size != self.page_size or (self.pages and time.time() - self.loaded_at > self.ttl):
                self.reset()
            self.page_size = page_size
            # 接口单次只给 ~20 条，凑够这个逻辑页要多少条就一直顺游标取
            need = page * page_size
            walked = 0
            while len(self._loaded_users()) < need and not self.exhausted and walked < FOLLOWING_WALK_MAX:
                await self._load_page(cookie, proxy, slot)
                walked += 1
            users = self._loaded_users()
            loaded_pages = max(1, math.ceil(len(users) / page_size))
            # 请求页超出已取范围（没走到底就撞上单次翻页上限）时返回最后一页，页码也回真实值
            effective_page = min(page, loaded_pages)
            has_more = loaded_pages > effective_page or not self.exhausted
            fetched_at = self.loaded_at
            total = self.pages[0]["total"] if self.pages else None

        if not users:
            return {"users": [], "page": effective_page, "page_size": page_size, "has_more": False, "total": total}
        start = (effective_page - 1) * page_size
        return {
            "users": users[start : start + page_size],
            "page": effective_page,
            "page_size": page_size,
            "has_more": has_more,
            "total": total,
            "max_page": loaded_pages,
            "fetched_at": fetched_at,
        }

    async def stream(
        self,
        cookie: str,
        proxy: str | None,
        slot: SlotFactory,
        web_rid: str,
        quality: str | None = None,
        refresh: bool = False,
    ) -> dict:
        """某个直播间的播放直链（短时缓存；``refresh=True`` 强制重取）。"""
        web_rid = str(web_rid).strip()
        key = (web_rid, quality or "")
        async with self._lock:
            self._bind(cookie)
            cached = self.streams.get(key)
            if not refresh and cached and time.time() - cached[0] < STREAM_URL_TTL:
                return dict(cached[1])
            if len(self.streams) >= STREAM_CACHE_MAX:
                self.streams.clear()
            async with slot():
                data = await fetch_live_stream_url(cookie, proxy, web_rid, quality)
            self.streams[key] = (time.time(), data)
            logger.debug(
                f"Douyin live stream resolved: {web_rid} live={data.get('is_live')} "
                f"playback={data.get('playback') or '-'} codec={data.get('codec') or '-'}"
            )
            return dict(data)

    async def live(
        self, cookie: str, proxy: str | None, slot: SlotFactory, refresh: bool = False
    ) -> dict:
        async with self._lock:
            self._bind(cookie)
            fresh = self.live_rooms is not None and time.time() - self.live_at < self.live_ttl
            if refresh or not fresh:
                async with slot():
                    self.live_rooms = await fetch_live_rooms(cookie, proxy)
                self.live_at = time.time()
                logger.debug(f"Douyin following-live refreshed: {len(self.live_rooms or [])} rooms")
            return {"rooms": list(self.live_rooms or []), "fetched_at": self.live_at}


_STORE = FollowingStore()


def get_store() -> FollowingStore:
    return _STORE


__all__ = [
    "DouyinFollowingError",
    "FollowingStore",
    "fetch_following_page",
    "fetch_live_rooms",
    "fetch_live_stream_url",
    "fetch_self_profile",
    "get_store",
    "normalize_following_page",
    "normalize_live_rooms",
    "normalize_self_profile",
    "normalize_stream_info",
]