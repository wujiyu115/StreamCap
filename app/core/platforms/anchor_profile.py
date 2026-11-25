"""主播主页跳转：把「直播间地址」映射成各平台的「主播主页地址」。

绝大多数平台的直播间页就是主播的主页（虎牙/斗鱼/快手/Twitch… 房间页既是频道页），
直接用原地址即可；只有两家必须查一次才能拿到主页地址：

- **抖音**：`live.douyin.com/<web_rid>` 里的 web_rid 是房间号，主页是
  `www.douyin.com/user/<sec_uid>`。用一次 room enter 原始返回里的
  `data.user.sec_uid`（和有效性检测同一个接口，不额外压风控预算）。
- **B 站**：`live.bilibili.com/<room_id>` 里的 room_id 是房间号，主页是
  `space.bilibili.com/<uid>`，查 `room/v1/Room/get_info` 拿 `data.uid`。

解析结果按 URL 缓存在内存里（sec_uid / uid 基本不变），TTL 30 天——重启后重查一次，
成本是每个房间一次请求。查不到时返回 ``kind="room"``（原直播间地址）兜底，不让入口点不动。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import httpx
import streamget

from ...utils.logger import logger

DOUYIN_PROFILE_URL = "https://www.douyin.com/user/{sec_uid}"
BILIBILI_PROFILE_URL = "https://space.bilibili.com/{uid}"
BILIBILI_ROOM_INFO_URL = "https://api.live.bilibili.com/room/v1/Room/get_info"

CACHE_TTL = 30 * 86400.0
CACHE_MAX = 2000
PC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

_CACHE: dict[str, tuple[dict, float]] = {}


@dataclass
class ProfileLink:
    url: str
    kind: str  # "profile" = 主播主页， "room" = 直播间（该平台房间页就是主页 / 解析失败兜底）
    cached: bool = False
    reason: str | None = None

    def to_dict(self) -> dict:
        return {"url": self.url, "kind": self.kind, "cached": self.cached, "reason": self.reason}


def platform_key_of(url: str) -> str | None:
    """按 URL 找平台 key（复用平台 handler 的注册表），找不到返回 None。

    注意别用 ``get_platform_handler()``：它的实例缓存键不含平台类，先建过的 douyin 实例会被
    后面所有 URL 复用（实测 bilibili 地址也返回 douyin），拿它判平台必错。
    """
    from .platform_handlers.base import PlatformHandler

    try:
        for pattern, handler_class in PlatformHandler.get_registered_patterns().items():
            if re.search(pattern, url):
                return getattr(handler_class, "platform", None)
    except Exception:  # 注册表匹配失败不该把跳转打挂
        return None
    return None


def douyin_web_rid(url: str) -> str | None:
    """`https://live.douyin.com/<web_rid>` → web_rid（带 query / 尾斜杠都能吃）。"""
    match = re.search(r"live\.douyin\.com/([A-Za-z0-9_.-]+)", url or "")
    return match.group(1) if match else None


def bilibili_room_id(url: str) -> str | None:
    match = re.search(r"live\.bilibili\.com/(\d+)", url or "")
    return match.group(1) if match else None


def _cache_get(key: str) -> tuple[dict, bool] | None:
    entry = _CACHE.get(key)
    if not entry:
        return None
    data, ts = entry
    if time.time() - ts > CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return data, True


def _cache_put(key: str, data: dict) -> None:
    if len(_CACHE) >= CACHE_MAX:
        oldest = sorted(_CACHE.items(), key=lambda kv: kv[1][1])[: CACHE_MAX // 4]
        for k, _ in oldest:
            _CACHE.pop(k, None)
    _CACHE[key] = (data, time.time())


def clear_cache() -> None:
    _CACHE.clear()


async def _resolve_douyin(url: str, proxy: str | None, cookies: str | None) -> str | None:
    live_stream = streamget.DouyinLiveStream(proxy_addr=proxy, cookies=cookies)
    raw = await live_stream.fetch_web_stream_data(url, process_data=False)
    if raw.get("status_code") != 0:
        logger.info(f"Douyin profile resolve: status_code={raw.get('status_code')} ({url})")
        return None
    user = (raw.get("data") or {}).get("user") or {}
    sec_uid = user.get("sec_uid")
    if not sec_uid:
        return None
    return DOUYIN_PROFILE_URL.format(sec_uid=sec_uid)


async def _resolve_bilibili(url: str, proxy: str | None) -> str | None:
    room_id = bilibili_room_id(url)
    if not room_id:
        return None
    async with httpx.AsyncClient(proxy=proxy, timeout=10) as client:
        resp = await client.get(
            BILIBILI_ROOM_INFO_URL,
            params={"room_id": room_id},
            headers={"User-Agent": PC_UA, "Referer": "https://live.bilibili.com/"},
        )
        payload = resp.json()
    data = payload.get("data") or {}
    uid = data.get("uid")
    if payload.get("code") != 0 or not uid:
        logger.info(f"Bilibili profile resolve failed: code={payload.get('code')} ({url})")
        return None
    return BILIBILI_PROFILE_URL.format(uid=uid)


async def resolve_profile_url(
    url: str,
    *,
    proxy: str | None = None,
    cookies: str | None = None,
    slot=None,
) -> ProfileLink:
    """把直播间地址解析成主播主页；解析不出来就退回直播间地址。

    ``slot`` 是可选的平台节奏门（返回 async 上下文管理器），抖音/ B 站这类要发请求的
    分支会套在里面，和周期监控共享同一份风控预算。
    """
    url = (url or "").strip()
    if not url:
        return ProfileLink(url=url, kind="room", reason="empty url")

    hit = _cache_get(url)
    if hit:
        data, _ = hit
        return ProfileLink(url=data["url"], kind=data["kind"], cached=True)

    platform_key = platform_key_of(url)
    resolved: str | None = None
    try:
        if platform_key == "douyin" and douyin_web_rid(url):
            if slot is not None:
                async with slot():
                    resolved = await _resolve_douyin(url, proxy, cookies)
            else:
                resolved = await _resolve_douyin(url, proxy, cookies)
        elif platform_key == "bilibili" and bilibili_room_id(url):
            if slot is not None:
                async with slot():
                    resolved = await _resolve_bilibili(url, proxy)
            else:
                resolved = await _resolve_bilibili(url, proxy)
    except Exception as exc:  # 解析失败只降级，不让入口报错
        logger.warning(f"Profile url resolve failed ({platform_key} {url}): {type(exc).__name__}: {exc}")
        return ProfileLink(url=url, kind="room", reason=f"{type(exc).__name__}: {exc}")

    if resolved:
        result = {"url": resolved, "kind": "profile"}
        _cache_put(url, result)
        return ProfileLink(url=resolved, kind="profile")
    return ProfileLink(url=url, kind="room", reason="unresolved")
