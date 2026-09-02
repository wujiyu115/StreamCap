from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from ...core.platforms.platform_handlers import get_platform_info
from ...models.recording.recording_model import Recording
from ...models.recording.recording_status_model import RecordingStatus
from ...utils.logger import logger
from ..deps import get_current_user, get_services
from ..schemas import (
    BatchCreateRequest,
    BatchDeleteRequest,
    BatchMonitorRequest,
    MonitorToggleRequest,
    RecordingCreate,
    RecordingUpdate,
)

router = APIRouter(prefix="/api/recordings", tags=["recordings"])

_ERROR_STATUSES = (RecordingStatus.RECORDING_ERROR, RecordingStatus.LIVE_STATUS_CHECK_ERROR)
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


def serialize_recording(recording: Recording) -> dict:
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
    platform, platform_key = get_platform_info(recording.url)
    if platform and platform_key:
        recording.platform = platform
        recording.platform_key = platform_key
    recording.loop_time_seconds = int(user_config.get("loop_time_seconds", 300))
    return recording


def _get_recording_or_404(rm, rec_id: str) -> Recording:
    recording = rm.find_recording_by_id(rec_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="recording not found")
    return recording


@router.get("")
async def list_recordings(user: str = Depends(get_current_user), services=Depends(get_services)):
    rm = services.recording_manager
    return {"recordings": [serialize_recording(r) for r in rm.recordings]}


@router.post("")
async def create_recording(
    body: RecordingCreate,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    rm = services.recording_manager
    if not body.url.strip():
        raise HTTPException(status_code=400, detail="url is required")

    platform, _ = get_platform_info(body.url.strip())
    if not platform:
        raise HTTPException(status_code=400, detail="unsupported url")

    recording = _build_recording(services, body)
    recording.update_title(rm._.get(recording.quality, recording.quality))
    await rm.add_recording(recording)
    recording.scheduled_time_range = await rm.get_scheduled_time_range(
        recording.scheduled_start_time, recording.monitor_hours
    )
    if recording.monitor_status:
        services.run_coro(rm.check_if_live(recording))
    return serialize_recording(recording)


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
        services.run_coro(rm.check_if_live(recording))
        created.append(serialize_recording(recording))

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
        raise HTTPException(status_code=400, detail="no fields to update")

    if "url" in updates:
        platform, platform_key = get_platform_info(updates["url"])
        if not platform:
            raise HTTPException(status_code=400, detail="unsupported url")
        recording.platform = platform
        recording.platform_key = platform_key

    recording.update(updates)
    recording.update_title(rm._.get(recording.quality, recording.quality))
    services.run_coro(rm.persist_recordings())
    return serialize_recording(recording)


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
    return serialize_recording(recording)


@router.post("/batch-monitor")
async def toggle_monitor_batch(
    body: BatchMonitorRequest,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    rm = services.recording_manager
    for rec_id in body.ids:
        recording = rm.find_recording_by_id(rec_id)
        if recording is None:
            continue
        services.run_coro(
            rm.start_monitor_recording(recording, auto_save=False)
            if body.enabled
            else rm.stop_monitor_recording(recording, auto_save=False)
        )
    services.run_coro(rm.persist_recordings())
    return {"ok": True, "count": len(body.ids)}


@router.post("/{rec_id}/stop")
async def stop_recording(
    rec_id: str,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    rm = services.recording_manager
    recording = _get_recording_or_404(rm, rec_id)
    rm.stop_recording(recording, manually_stopped=True)
    return serialize_recording(recording)


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
