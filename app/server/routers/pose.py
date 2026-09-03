from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ...core.pose.pose_params import PoseParams
from ...core.pose.pose_task_manager import TaskBusyError
from .. import media_service
from ..deps import get_current_user, get_services
from ..schemas import PoseTaskSubmitRequest

router = APIRouter(prefix="/api/pose", tags=["pose"])


def _task_manager(request: Request):
    manager = getattr(request.app.state, "pose_task_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="pose task manager unavailable")
    return manager


def _is_clip_product(path: str, merged_suffix: str) -> bool:
    """带产物后缀（如 _merged）的文件是识别剪辑产物，重处理会套娃。"""
    return os.path.splitext(os.path.basename(path))[0].endswith(merged_suffix or "_merged")


@router.post("/tasks")
async def submit_task(
    request: Request,
    body: PoseTaskSubmitRequest,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    manager = _task_manager(request)
    media_root = services.settings_config.get_video_save_path()

    try:
        paths = [media_service.resolve_safe(media_root, p) for p in body.paths]
    except PermissionError:
        raise HTTPException(status_code=403, detail="access denied")

    videos = [p for p in paths if os.path.isfile(p)]
    if not videos:
        raise HTTPException(status_code=400, detail="no video files found")

    params = PoseParams.from_user_config(
        {**(services.settings_config.user_config.get("pose_detection") or {}), **(body.overrides or {})}
    )

    merged_suffix = params.merged_suffix or "_merged"
    videos = [p for p in videos if not _is_clip_product(p, merged_suffix)]
    if not videos:
        raise HTTPException(status_code=400, detail="no video files found (clip products are skipped)")

    try:
        result = manager.submit(
            videos=videos,
            media_root=media_root,
            params=params.to_dict(),
            trigger=body.trigger,
            wait_file=False,
        )
    except TaskBusyError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return result


@router.get("/tasks")
async def list_tasks(request: Request, user: str = Depends(get_current_user)):
    return {"tasks": _task_manager(request).list_tasks()}


@router.post("/tasks/{task_id}/stop")
async def stop_task(request: Request, task_id: str, user: str = Depends(get_current_user)):
    try:
        return _task_manager(request).stop()
    except TaskBusyError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/tasks/{task_id}/log")
async def read_log(
    request: Request,
    task_id: str,
    offset: int = Query(default=0, ge=0),
    user: str = Depends(get_current_user),
):
    chunk, next_offset = _task_manager(request).read_log(task_id, offset)
    return {"chunk": chunk, "next_offset": next_offset}
