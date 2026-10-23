from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..deps import get_current_user

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
