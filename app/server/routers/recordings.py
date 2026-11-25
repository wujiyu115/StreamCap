from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from ...core.platforms import anchor_profile
from ...core.platforms.platform_handlers import get_platform_info
from ...core.platforms.room_validity import resolve_platform_proxy
from ...models.recording.recording_model import Recording
from ...models.recording.recording_status_model import RecordingStatus
from ...utils.logger import logger
from .. import error_codes as errors
from ..deps import get_current_user, get_services
from ..schemas import (
    BatchCreateRequest,
    BatchDeleteRequest,
    BatchMonitorRequest,
    MonitorToggleRequest,
    RecordingCreate,
    RecordingUpdate,
    ValidityCheckRequest,
)

router = APIRouter(prefix="/api/recordings", tags=["recordings"])

_ERROR_STATUSES = (RecordingStatus.RECORDING_ERROR, RecordingStatus.LIVE_STATUS_CHECK_ERROR)
# 解析主播主页的超时：抖音 room enter 正常 1~2s，超了就降级成直播间地址
PROFILE_URL_TIMEOUT = 20.0
BATCH_QUALITY_MAP = {"0": "OD", "1": "UHD", "2": "HD", "3": "SD", "4": "LD"}


def compute_card_state(recording: Recording) -> str:
    if recording.is_recording:
        return "recording"
    if recording.status_info in _ERROR_STATUSES:
        return "error"
    if recording.is_checking:
        return "checking"
    if recording.is_live and recording.monitor_status and not recording.is_recording:
        return "live"
    if not recording.is_live and recording.monitor_status and recording.status_info != RecordingStatus.NOT_IN_SCHEDULED_CHECK:
        return "offline"
    if not recording.monitor_status or recording.status_info == RecordingStatus.NOT_IN_SCHEDULED_CHECK:
        return "stopped"
    return "unknown"


def _media_path(recording: Recording, root: str | None) -> str | None:
    """recording_dir 相对媒体根的路径，供前端跳转视频管理对应目录。"""
    rd = recording.recording_dir
    if not rd or not root:
        return None
    try:
        rel = os.path.relpath(os.path.abspath(rd), os.path.abspath(root))
    except ValueError:
        return None
    if rel.startswith(".."):
        return ""
    return rel.replace(os.sep, "/")


def serialize_recording(recording: Recording, media_root: str | None = None) -> dict:
    data = recording.to_dict()
    data.update(
        {
            "title": recording.title,
            "display_title": recording.display_title,
            "live_title": recording.live_title,
            "status_info": recording.status_info,
            "is_live": recording.is_live,
            "is_recording": recording.is_recording,
            "is_checking": recording.is_checking,
            "manually_stopped": recording.manually_stopped,
            "stopping_in_progress": recording.stopping_in_progress,
            "speed": recording.speed,
            "start_time": recording.start_time.isoformat() if recording.start_time else None,
            "cumulative_duration_seconds": recording.cumulative_duration.total_seconds(),
            "last_duration_seconds": recording.last_duration.total_seconds(),
            "scheduled_time_range": recording.scheduled_time_range,
            "record_url": recording.record_url,
            "current_output_file": recording.current_output_file,
            "state": compute_card_state(recording),
            "unsupported": recording.unsupported,
            "consecutive_failures": recording.consecutive_failures,
            "media_path": _media_path(recording, media_root),
        }
    )
    return data


def _build_recording(services, body: RecordingCreate) -> Recording:
    user_config = services.settings_config.user_config
    recording = Recording(
        rec_id=str(uuid.uuid4()),
        url=body.url.strip(),
        streamer_name=body.streamer_name.strip(),
        quality=body.quality,
        record_format=body.record_format,
        segment_record=user_config.get("segmented_recording_enabled", False)
        if body.segment_record is None
        else body.segment_record,
        segment_time=int(body.segment_time or user_config.get("video_segment_time", 1800)),
        monitor_status=body.monitor_status,
        scheduled_recording=body.scheduled_recording,
        scheduled_start_time=body.scheduled_start_time,
        monitor_hours=body.monitor_hours,
        recording_dir=body.recording_dir,
        enabled_message_push=body.enabled_message_push,
        only_notify_no_record=body.only_notify_no_record,
        flv_use_direct_download=bool(
            body.flv_use_direct_download
            if body.flv_use_direct_download is not None
            else user_config.get("flv_use_direct_download", False)
        ),
        video_bitrate=body.video_bitrate,
        pose_enabled=body.pose_enabled,
    )
    recording.special_attention = bool(body.special_attention)
    platform, platform_key = get_platform_info(recording.url)
    if platform and platform_key:
        recording.platform = platform
        recording.platform_key = platform_key
    recording.loop_time_seconds = int(user_config.get("loop_time_seconds", 300))
    return recording


def _get_recording_or_404(rm, rec_id: str) -> Recording:
    recording = rm.find_recording_by_id(rec_id)
    if recording is None:
        raise HTTPException(status_code=404, detail=errors.RECORDING_NOT_FOUND)
    return recording


@router.get("")
async def list_recordings(user: str = Depends(get_current_user), services=Depends(get_services)):
    rm = services.recording_manager
    return {"recordings": [serialize_recording(r, media_root=services.settings_config.get_video_save_path()) for r in rm.recordings]}


@router.post("")
async def create_recording(
    body: RecordingCreate,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    rm = services.recording_manager
    if not body.url.strip():
        raise HTTPException(status_code=400, detail=errors.URL_REQUIRED)

    platform, _ = get_platform_info(body.url.strip())
    if not platform:
        raise HTTPException(status_code=400, detail=errors.UNSUPPORTED_URL)

    recording = _build_recording(services, body)
    recording.update_title(rm._.get(recording.quality, recording.quality))
    await rm.add_recording(recording)
    recording.scheduled_time_range = await rm.get_scheduled_time_range(
        recording.scheduled_start_time, recording.monitor_hours
    )
    if recording.monitor_status:
        services.run_coro(rm.check_if_live(recording, priority=True))
    return serialize_recording(recording, media_root=services.settings_config.get_video_save_path())


@router.post("/batch")
async def create_recordings_batch(
    body: BatchCreateRequest,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    rm = services.recording_manager
    user_config = services.settings_config.user_config
    created = []
    for line in body.lines:
        line = line.strip().replace("，", ",")
        if "http" not in line:
            continue
        parts = [p for p in line.split(",") if p]
        quality, url, streamer_name = "OD", "", ""
        if len(parts) == 3:
            quality, url, streamer_name = parts
        elif len(parts) == 2:
            if parts[1].startswith("http"):
                quality, url = parts
            else:
                url, streamer_name = parts
        else:
            url = parts[0]

        url = url.strip()
        platform, _ = get_platform_info(url)
        if not platform:
            logger.warning(f"Batch add skipped unsupported url: {url}")
            continue

        quality = BATCH_QUALITY_MAP.get(quality.strip(), quality.strip() or "OD")
        recording = Recording(
            rec_id=str(uuid.uuid4()),
            url=url,
            streamer_name=streamer_name.strip(),
            quality=quality,
            record_format=user_config.get("video_format", "TS"),
            segment_record=user_config.get("segmented_recording_enabled", False),
            segment_time=user_config.get("video_segment_time", "1800"),
            monitor_status=True,
            scheduled_recording=False,
            scheduled_start_time=None,
            monitor_hours=None,
            recording_dir=None,
            enabled_message_push=False,
            only_notify_no_record=user_config.get("only_notify_no_record", False),
            flv_use_direct_download=user_config.get("flv_use_direct_download", False),
        )
        p, pk = get_platform_info(recording.url)
        if p and pk:
            recording.platform = p
            recording.platform_key = pk
        recording.loop_time_seconds = int(user_config.get("loop_time_seconds", 300))
        recording.update_title(rm._.get(recording.quality, recording.quality))
        await rm.add_recording(recording)
        recording.scheduled_time_range = await rm.get_scheduled_time_range(
            recording.scheduled_start_time, recording.monitor_hours
        )
        services.run_coro(rm.check_if_live(recording, priority=True))
        created.append(serialize_recording(recording, media_root=services.settings_config.get_video_save_path()))

    return {"created": len(created), "recordings": created}


@router.put("/{rec_id}")
async def update_recording(
    rec_id: str,
    body: RecordingUpdate,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    rm = services.recording_manager
    recording = _get_recording_or_404(rm, rec_id)

    raw = body.model_dump(exclude_unset=True)
    updates = {k: v for k, v in raw.items() if v is not None or k == "pose_enabled"}
    if not updates:
        raise HTTPException(status_code=400, detail=errors.NO_FIELDS_TO_UPDATE)

    if "url" in updates:
        platform, platform_key = get_platform_info(updates["url"])
        if not platform:
            raise HTTPException(status_code=400, detail=errors.UNSUPPORTED_URL)
        recording.platform = platform
        recording.platform_key = platform_key

    recording.update(updates)
    recording.update_title(rm._.get(recording.quality, recording.quality))
    # 编辑视为人工干预：重置失败计数/退避/unsupported，恢复轮询
    recording.consecutive_failures = 0
    recording.backoff_multiplier = 1
    recording.unsupported = False
    recording.next_check_after = 0.0
    # 重新开启监控或换 URL（新房间）时重置未开播宽限期
    if updates.get("monitor_status") or "url" in updates:
        recording.last_live_time = time.time()
    if updates.get("monitor_status"):
        # 编辑里开启监控等同手动开启：立即走优先检查
        services.run_coro(rm.check_if_live(recording, priority=True))
    if {"scheduled_recording", "scheduled_start_time", "monitor_hours"} & updates.keys():
        # 窗口是 start+时长现算的，改完不重算就会用回上一次的值（监控没开时压根没人算）
        recording.scheduled_time_range = await rm.get_scheduled_time_range(
            recording.scheduled_start_time, recording.monitor_hours
        )
    services.run_coro(rm.persist_recordings())
    return serialize_recording(recording, media_root=services.settings_config.get_video_save_path())


@router.delete("/{rec_id}")
async def delete_recording(
    rec_id: str,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    rm = services.recording_manager
    recording = _get_recording_or_404(rm, rec_id)

    if recording.is_recording or recording.rec_id in rm.active_recorders:
        rm.stop_recording(recording, manually_stopped=True)

    await rm.remove_recording(recording)
    return {"ok": True}


@router.post("/batch-delete")
async def delete_recordings_batch(
    body: BatchDeleteRequest,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    rm = services.recording_manager
    deleted, not_found = [], []
    for rec_id in body.ids:
        recording = rm.find_recording_by_id(rec_id)
        if recording is None:
            not_found.append(rec_id)
            continue
        if recording.is_recording or recording.rec_id in rm.active_recorders:
            rm.stop_recording(recording, manually_stopped=True)
        await rm.remove_recording(recording)
        deleted.append(rec_id)
    return {"deleted": len(deleted), "not_found": not_found}


@router.get("/validity")
async def get_validity_snapshot(
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    """检测结果快照：全部缓存条目 + 待检数，不发任何检测请求。"""
    fut = services.run_coro(services.recording_manager.check_room_validity(limit=0))
    if fut is None:
        raise HTTPException(status_code=500, detail=errors.VALIDITY_CHECK_UNAVAILABLE)
    return await asyncio.wrap_future(fut)


@router.get("/{rec_id}/profile-url")
async def get_recording_profile_url(
    rec_id: str,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    """主播主页地址（点主播名旁边的小图标时用）。

    绝大多数平台直播间页就是主播主页，只有抖音 / B 站要查一次（抖音：room enter 的
    `data.user.sec_uid` → `www.douyin.com/user/<sec_uid>`；B 站：`room/get_info` 的
    `data.uid`）。查询走后台 loop 的平台节奏门（和周期监控共享风控预算），结果按 URL
    缓存在内存里；查不到就降级成直播间地址，保证入口点得动。
    """
    rm = services.recording_manager
    recording = _get_recording_or_404(rm, rec_id)
    url = recording.url
    platform_key = anchor_profile.platform_key_of(url)
    if platform_key == "douyin":
        cookie = services.settings_config.get_cookies_value("douyin") or ""
    else:
        cookie = None
    proxy = resolve_platform_proxy(services.settings_config.user_config, platform_key)

    async def _run():
        return await anchor_profile.resolve_profile_url(
            url,
            proxy=proxy,
            cookies=cookie,
            slot=(lambda: rm.platform_slot(platform_key)) if platform_key else None,
        )

    coro = _run()
    future = services.run_coro(coro)
    link = None
    if future is None:
        coro.close()  # 后台 loop 不可用时别把协程漏成 RuntimeWarning
    else:
        try:
            link = await asyncio.wait_for(asyncio.wrap_future(future), timeout=PROFILE_URL_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(f"Profile url resolve timed out: {url}")
        except Exception as exc:  # 解析失败只降级，不把跳转入口打成 500
            logger.warning(f"Profile url resolve failed: {type(exc).__name__}: {exc}")

    payload = link.to_dict() if link else {"url": url, "kind": "room", "cached": False, "reason": "unavailable"}
    payload["platform_key"] = platform_key
    return payload


@router.post("/check-validity")
async def check_recordings_validity(
    body: ValidityCheckRequest,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    """检测录制配置的直播间是否仍存在（抖音精确判定已失效；其他平台尽力而为）。

    调度到后台 loop 执行以与周期监控共享平台信号量，再跨 loop 等待结果。
    """
    fut = services.run_coro(
        services.recording_manager.check_room_validity(
            body.ids or None, force=body.force, force_since=body.force_since, limit=body.limit
        )
    )
    if fut is None:
        raise HTTPException(status_code=500, detail=errors.VALIDITY_CHECK_UNAVAILABLE)
    return await asyncio.wrap_future(fut)


@router.post("/{rec_id}/monitor")
async def toggle_monitor(
    rec_id: str,
    body: MonitorToggleRequest,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    rm = services.recording_manager
    recording = _get_recording_or_404(rm, rec_id)

    if body.enabled:
        await rm.start_monitor_recording(recording)
    else:
        await rm.stop_monitor_recording(recording)
    return serialize_recording(recording, media_root=services.settings_config.get_video_save_path())


@router.post("/batch-monitor")
async def toggle_monitor_batch(
    body: BatchMonitorRequest,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    """批量开/关监控。

    老实现把协程丢给 run_coro 就不管了，`count` 取的是**入参长度**：rec_id 对不上就
    静默跳过，协程里抛的异常又没人取 future，于是永远回 `{"ok": true, "count": N}`
    ——170 条任务里哪怕一条没生效，从返回值也看不出来（2026-11-24 排查「关监控没
    反应」时踩到，实际是 locale 缺失导致协程崩）。这里逐条 await 真跑完，没找到的、
    失败的都单独列出来。
    """
    rm = services.recording_manager
    pending = []
    missing: list[str] = []
    failed: list[dict] = []
    for rec_id in body.ids:
        recording = rm.find_recording_by_id(rec_id)
        if recording is None:
            missing.append(rec_id)
            continue
        coro = (
            rm.start_monitor_recording(recording, auto_save=False)
            if body.enabled
            else rm.stop_monitor_recording(recording, auto_save=False)
        )
        fut = services.run_coro(coro)
        if fut is None:
            failed.append({"id": rec_id, "error": "backend loop unavailable"})
            continue
        pending.append((rec_id, fut))

    applied = 0
    for rec_id, fut in pending:
        try:
            await asyncio.wrap_future(fut)
            applied += 1
        except Exception as exc:  # 单条失败不影响其他条
            failed.append({"id": rec_id, "error": f"{type(exc).__name__}: {exc}"})

    persist = services.run_coro(rm.persist_recordings())
    if persist is not None:
        try:
            await asyncio.wrap_future(persist)
        except Exception as exc:
            logger.warning(f"批量开关监控落盘失败: {exc}")

    ok = applied == len(body.ids)
    if not ok:
        action = "开启" if body.enabled else "关闭"
        logger.warning(
            f"批量{action}监控未全部生效：{applied}/{len(body.ids)}"
            f"（未找到 {len(missing)}，失败 {len(failed)}）"
        )
    return {
        "ok": ok,
        "count": applied,
        "requested": len(body.ids),
        "missing": missing,
        "failed": failed,
    }


@router.post("/{rec_id}/stop")
async def stop_recording(
    rec_id: str,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    rm = services.recording_manager
    recording = _get_recording_or_404(rm, rec_id)
    rm.stop_recording(recording, manually_stopped=True)
    return serialize_recording(recording, media_root=services.settings_config.get_video_save_path())


@router.get("/statuses")
async def recording_statuses(user: str = Depends(get_current_user), services=Depends(get_services)):
    """轻量状态快照，供前端高频轮询。"""
    rm = services.recording_manager
    items = []
    for r in rm.recordings:
        items.append(
            {
                "rec_id": r.rec_id,
                "state": compute_card_state(r),
                "status_info": r.status_info,
                "is_recording": r.is_recording,
                "is_live": r.is_live,
                "is_checking": r.is_checking,
                "monitor_status": r.monitor_status,
                "speed": r.speed,
                "live_title": r.live_title,
                "start_time": r.start_time.isoformat() if r.start_time else None,
                "cumulative_duration_seconds": r.cumulative_duration.total_seconds(),
                "last_duration_seconds": r.last_duration.total_seconds(),
            }
        )
    return {"recordings": items, "recording_enabled": services.recording_enabled, "server_time": datetime.now().isoformat()}
