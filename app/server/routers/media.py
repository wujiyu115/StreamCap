from __future__ import annotations

import mimetypes
import os
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .. import media_service
from ..deps import get_current_user, get_services
from ..schemas import MediaBatchDeleteRequest, MediaCleanRequest

router = APIRouter(prefix="/api/media", tags=["media"])

STREAM_CHUNK_SIZE = 1024 * 1024

_EXT_TYPE = {
    ".ts": "video/mp2t",
    ".m2ts": "video/mp2t",
    ".mts": "video/mp2t",
    ".mkv": "video/x-matroska",
    ".m4v": "video/x-m4v",
    ".ogv": "video/ogg",
    ".flv": "video/x-flv",
}


def _guess_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in _EXT_TYPE:
        return _EXT_TYPE[ext]
    t, _ = mimetypes.guess_type(path)
    return t or "application/octet-stream"


def _parse_range(range_header: str, file_size: int) -> tuple[int, int]:
    if not range_header.startswith("bytes=") or "," in range_header or file_size <= 0:
        raise ValueError("invalid range")
    spec = range_header.removeprefix("bytes=").strip()
    if spec.count("-") != 1:
        raise ValueError("invalid range")
    start_text, end_text = spec.split("-", maxsplit=1)
    if not start_text and not end_text:
        raise ValueError("invalid range")
    if (start_text and not start_text.isdecimal()) or (end_text and not end_text.isdecimal()):
        raise ValueError("invalid range")
    if not start_text:
        suffix_length = int(end_text)
        if suffix_length <= 0:
            raise ValueError("invalid range")
        return max(0, file_size - suffix_length), file_size - 1
    start = int(start_text)
    if start >= file_size:
        raise ValueError("range beyond end of file")
    end = int(end_text) if end_text else file_size - 1
    if end < start:
        raise ValueError("invalid range")
    return start, min(end, file_size - 1)


async def _send_range(path: Path, start: int, end: int):
    async with aiofiles.open(path, "rb") as file:
        await file.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = await file.read(min(STREAM_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _root(services) -> str:
    return services.settings_config.get_video_save_path()


@router.get("/tree")
async def tree(path: str = Query(""), user: str = Depends(get_current_user), services=Depends(get_services)):
    try:
        return media_service.list_dir(path, _root(services))
    except PermissionError:
        raise HTTPException(status_code=403, detail="access denied")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="not found")


@router.get("/stats")
async def stats(path: str = Query(""), user: str = Depends(get_current_user), services=Depends(get_services)):
    try:
        result = media_service.stats(path, _root(services))
    except PermissionError:
        raise HTTPException(status_code=403, detail="access denied")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="not found")

    protected: list[str] = []
    rm = services.recording_manager
    if rm is not None:
        protected = [r.current_output_file for r in rm.recordings if r.current_output_file]
    result["protected_files"] = protected
    return result


@router.get("/stream")
async def stream(request: Request, path: str = Query(...), user: str = Depends(get_current_user), services=Depends(get_services)):
    try:
        full = media_service.resolve_safe(_root(services), path)
    except PermissionError:
        raise HTTPException(status_code=403, detail="access denied")
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="file not found")

    size = os.path.getsize(full)
    ctype = _guess_type(full)
    range_header = request.headers.get("range")
    if not range_header:
        return StreamingResponse(
            _send_range(Path(full), 0, size - 1),
            media_type=ctype,
            headers={"Accept-Ranges": "bytes", "Content-Length": str(size)},
        )

    try:
        start, end = _parse_range(range_header, size)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad range")

    headers = {
        "Content-Range": f"bytes {start}-{end}/{size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
    }
    return StreamingResponse(_send_range(Path(full), start, end), status_code=206, media_type=ctype, headers=headers)


@router.delete("")
async def delete_media(path: str = Query(...), user: str = Depends(get_current_user), services=Depends(get_services)):
    root = _root(services)
    try:
        media_service.delete_one(path, root)
    except PermissionError:
        raise HTTPException(status_code=403, detail="access denied")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True}


@router.post("/batch-delete")
async def batch_delete(
    body: MediaBatchDeleteRequest,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    return media_service.delete_paths(body.paths, _root(services))


@router.post("/clean")
async def clean(
    body: MediaCleanRequest,
    user: str = Depends(get_current_user),
    services=Depends(get_services),
):
    try:
        return media_service.clean_small(body.path, body.max_bytes, _root(services))
    except PermissionError:
        raise HTTPException(status_code=403, detail="access denied")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="not found")
