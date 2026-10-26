from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import media_service
from ..deps import get_current_user, get_services
from ..schemas import UploadEnqueueRequest

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


def _manager(request: Request):
    manager = getattr(request.app.state, "upload_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="err.uploadUnavailable")
    return manager


@router.get("")
async def list_uploads(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(15, ge=1, le=100),
    user: str = Depends(get_current_user),
):
    manager = _manager(request)
    return {
        "jobs": manager.list_jobs(page, per_page),
        "channels": manager.list_channels(),
        "total": manager.count_jobs(),
        "page": page,
        "per_page": per_page,
    }


@router.get("/channels")
async def list_channels(request: Request, user: str = Depends(get_current_user)):
    return {"channels": _manager(request).list_channels()}


@router.post("/enqueue")
async def enqueue_uploads(
    body: UploadEnqueueRequest,
    request: Request,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    """把选中的文件/文件夹加进上传队列：每个视频文件一条任务，按渠道去重跳过已上传的。"""
    manager = _manager(request)
    if not body.paths:
        raise HTTPException(status_code=400, detail="err.uploadNoFiles")
    if not body.providers:
        raise HTTPException(status_code=400, detail="err.uploadNoChannel")
    root = services.settings_config.get_video_save_path()
    # os.walk 走 NAS 很慢，必须丢线程池，否则整个事件循环被卡住
    collected = await asyncio.to_thread(media_service.collect_videos, body.paths, root)
    if not collected["files"]:
        raise HTTPException(status_code=400, detail="err.uploadNoVideo")
    result = manager.enqueue_files(collected["files"], body.providers)
    if not result["providers"]:
        raise HTTPException(status_code=404, detail="err.uploadChannelNotFound")
    result["missing"] = collected["missing"]
    return result


@router.post("/{job_id}/retry")
async def retry_upload(job_id: str, request: Request, user: str = Depends(get_current_user)):
    if not _manager(request).retry(job_id):
        raise HTTPException(status_code=404, detail="err.uploadJobNotFound")
    return {"ok": True}


@router.post("/channels/{provider}/resume")
async def resume_channel(provider: str, request: Request, user: str = Depends(get_current_user)):
    if not _manager(request).resume_channel(provider):
        raise HTTPException(status_code=404, detail="err.uploadChannelNotFound")
    return {"ok": True}
