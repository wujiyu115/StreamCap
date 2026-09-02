from __future__ import annotations

from fastapi import APIRouter, Depends

from ...core.update.update_checker import UpdateChecker
from .. import media_service
from ..deps import get_current_user, get_services

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/info")
async def system_info(user: str = Depends(get_current_user), services=Depends(get_services)):
    about = services.config_manager.load_about_config()
    latest = {}
    version_updates = about.get("version_updates") or []
    if version_updates:
        latest = version_updates[0]
    return {
        "version": latest.get("version"),
        "kernel_version": latest.get("kernel_version"),
        "release_date": latest.get("release_date"),
        "updates": latest.get("updates"),
        "announcement": latest.get("announcement"),
        "introduction": about.get("introduction"),
        "open_source_license": about.get("open_source_license"),
    }


@router.get("/stats")
async def system_stats(user: str = Depends(get_current_user), services=Depends(get_services)):
    rm = services.recording_manager
    recordings = rm.recordings if rm is not None else []
    total = len(recordings)
    active = sum(1 for r in recordings if r.is_recording)
    monitoring = sum(1 for r in recordings if r.monitor_status)

    storage = {"total_files": 0, "video_files": 0, "total_bytes": 0, "total_size": "0 B"}
    try:
        storage = media_service.stats("", services.settings_config.get_video_save_path())
    except (PermissionError, FileNotFoundError):
        pass

    return {
        "total_recordings": total,
        "active_recordings": active,
        "monitoring_recordings": monitoring,
        "stopped_monitoring": total - monitoring,
        "recording_enabled": services.recording_enabled,
        "storage": storage,
    }


@router.post("/check-update")
async def check_update(user: str = Depends(get_current_user), services=Depends(get_services)):
    checker = UpdateChecker(services.run_path)
    return await checker.check_for_updates()
