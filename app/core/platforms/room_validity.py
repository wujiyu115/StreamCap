"""直播间有效性检测：抖音走原始接口精确判定"已失效"，其他平台尽力而为。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import streamget

from ...utils.logger import logger
from . import platform_handlers

STATUS_LIVE = "live"
STATUS_OFFLINE = "offline"
STATUS_INVALID = "invalid"
STATUS_ERROR = "error"

# 抖音房间 ID 已失效的业务错误码（10011=当前服务繁忙 4001038=该内容暂时无法查看）
DOUYIN_INVALID_STATUS_CODES = {10011, 4001038}
DOUYIN_INVALID_RETRY_DELAY = 1.5


@dataclass
class RoomValidityResult:
    status: str
    anchor_name: str | None = None
    title: str | None = None
    detail: str | None = None
    precise: bool = False

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "anchor_name": self.anchor_name,
            "title": self.title,
            "detail": self.detail,
            "precise": self.precise,
        }


def resolve_platform_proxy(user_config: dict, platform_key: str | None) -> str | None:
    proxy_platforms = user_config.get("default_platform_with_proxy", "").replace("，", ",").replace(" ", "").split(",")
    if user_config.get("enable_proxy") and platform_key in proxy_platforms:
        return user_config.get("proxy_address")
    return None


async def check_room_validity(
    url: str,
    platform: str | None = None,
    platform_key: str | None = None,
    proxy: str | None = None,
    cookies: str | None = None,
    record_quality: str | None = None,
    account: dict | None = None,
) -> RoomValidityResult:
    if platform_key == "douyin" and "live.douyin.com" in url:
        return await check_douyin_room(url, proxy=proxy, cookies=cookies)
    return await check_room_generic(
        url, platform=platform, proxy=proxy, cookies=cookies, record_quality=record_quality, account=account
    )


async def check_douyin_room(url: str, proxy: str | None = None, cookies: str | None = None) -> RoomValidityResult:
    """通过 webcast/room/web/enter 原始返回精确判定抖音房间是否存在。"""
    for attempt in range(2):
        result = await _douyin_single_check(url, proxy, cookies)
        if result.status != STATUS_INVALID or attempt == 1:
            return result
        logger.info(f"Douyin room validity check retrying ({url}): {result.detail}")
        await asyncio.sleep(DOUYIN_INVALID_RETRY_DELAY)
    return result


async def _douyin_single_check(url: str, proxy: str | None, cookies: str | None) -> RoomValidityResult:
    live_stream = streamget.DouyinLiveStream(proxy_addr=proxy, cookies=cookies)
    try:
        raw = await live_stream.fetch_web_stream_data(url, process_data=False)
    except Exception as e:
        return RoomValidityResult(status=STATUS_ERROR, detail=f"{type(e).__name__}: {e}")

    status_code = raw.get("status_code")
    data = raw.get("data") or {}
    rooms = data.get("data") or []
    room = rooms[0] if rooms else {}
    prompts = data.get("prompts")

    if status_code == 0 and room.get("id_str"):
        anchor_name = (data.get("user") or {}).get("nickname")
        return RoomValidityResult(
            status=STATUS_LIVE if room.get("status") == 2 else STATUS_OFFLINE,
            anchor_name=anchor_name,
            title=room.get("title") or None,
            precise=True,
        )

    if status_code in DOUYIN_INVALID_STATUS_CODES:
        return RoomValidityResult(status=STATUS_INVALID, detail=f"{prompts}（错误码 {status_code}）", precise=True)

    if status_code == 0:
        return RoomValidityResult(status=STATUS_INVALID, detail=prompts or "房间不存在", precise=True)

    return RoomValidityResult(status=STATUS_ERROR, detail=f"status_code={status_code} {prompts or ''}".strip())


async def check_room_generic(
    url: str,
    platform: str | None = None,
    proxy: str | None = None,
    cookies: str | None = None,
    record_quality: str | None = None,
    account: dict | None = None,
) -> RoomValidityResult:
    """通用检测：复用平台 handler 取流。handler 异常被 trace_error_decorator 吞为 []，
    只能区分 房间存在（live/offline）与 检测失败，无法精确判定失效。"""
    account = account or {}
    handler = platform_handlers.get_platform_handler(
        live_url=url,
        proxy=proxy,
        cookies=cookies,
        record_quality=record_quality,
        platform=platform,
        username=account.get("username"),
        password=account.get("password"),
        account_type=account.get("account_type"),
    )
    if not handler:
        return RoomValidityResult(status=STATUS_ERROR, detail="未找到平台处理器")

    stream_info = await handler.get_stream_info(url)
    if stream_info and getattr(stream_info, "anchor_name", None):
        return RoomValidityResult(
            status=STATUS_LIVE if stream_info.is_live else STATUS_OFFLINE,
            anchor_name=stream_info.anchor_name,
            title=stream_info.title or None,
        )
    return RoomValidityResult(status=STATUS_ERROR, detail="接口未返回有效数据")
