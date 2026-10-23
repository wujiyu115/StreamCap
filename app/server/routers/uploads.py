from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..deps import get_current_user

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


def _manager(request: Request):
    manager = getattr(request.app.state, "upload_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="err.uploadUnavailable")
    return manager


@router.get("")
async def list_uploads(request: Request, limit: int = 50, user: str = Depends(get_current_user)):
    manager = _manager(request)
    return {"jobs": manager.list_jobs(limit), "channels": manager.list_channels()}


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
