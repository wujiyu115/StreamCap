from __future__ import annotations

import mimetypes
import os
from email.utils import formatdate
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from .. import error_codes as errors, media_service
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


class _RangeUnsatisfiable(Exception):
    """Range 语法合法但超出文件范围（应答 416）"""


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
        raise _RangeUnsatisfiable()
    end = int(end_text) if end_text else file_size - 1
    if end < start:
        raise ValueError("invalid range")
    return start, min(end, file_size - 1)


def _etag(stat: os.stat_result) -> str:
    return f'"{stat.st_size}-{stat.st_mtime_ns}"'


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
        raise HTTPException(status_code=403, detail=errors.ACCESS_DENIED)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=errors.NOT_FOUND)


@router.get("/stats")
async def stats(path: str = Query(""), user: str = Depends(get_current_user), services=Depends(get_services)):
    try:
        result = media_service.stats(path, _root(services))
    except PermissionError:
        raise HTTPException(status_code=403, detail=errors.ACCESS_DENIED)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=errors.NOT_FOUND)

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
        raise HTTPException(status_code=403, detail=errors.ACCESS_DENIED)
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail=errors.FILE_NOT_FOUND)

    st = os.stat(full)
    size = st.st_size
    ctype = _guess_type(full)
    # 协商缓存：文件内容未变（size+mtime 一致）时浏览器可直接命中本地缓存
    etag = _etag(st)
    cache_headers = {
        "ETag": etag,
        "Last-Modified": formatdate(st.st_mtime, usegmt=True),
        "Cache-Control": "private, no-cache",
    }
    range_header = request.headers.get("range")

    if not range_header:
        inm = request.headers.get("if-none-match")
        if inm and etag in [tag.strip() for tag in inm.split(",")]:
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "private, no-cache"})
        return StreamingResponse(
            _send_range(Path(full), 0, size - 1),
            media_type=ctype,
            headers={"Accept-Ranges": "bytes", "Content-Length": str(size), **cache_headers},
        )

    try:
        start, end = _parse_range(range_header, size)
    except _RangeUnsatisfiable:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
        )
    except ValueError:
        raise HTTPException(status_code=400, detail=errors.BAD_RANGE)

    # If-Range：ETag 不匹配（文件已变）时忽略 Range 回退全量 200
    if_range = request.headers.get("if-range")
    if if_range and if_range.strip() != etag:
        return StreamingResponse(
            _send_range(Path(full), 0, size - 1),
            media_type=ctype,
            headers={"Accept-Ranges": "bytes", "Content-Length": str(size), **cache_headers},
        )

    headers = {
        "Content-Range": f"bytes {start}-{end}/{size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        **cache_headers,
    }
    return StreamingResponse(_send_range(Path(full), start, end), status_code=206, media_type=ctype, headers=headers)


@router.delete("")
async def delete_media(path: str = Query(...), user: str = Depends(get_current_user), services=Depends(get_services)):
    root = _root(services)
    try:
        media_service.delete_one(path, root)
    except PermissionError:
        raise HTTPException(status_code=403, detail=errors.ACCESS_DENIED)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=errors.NOT_FOUND)
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
        raise HTTPException(status_code=403, detail=errors.ACCESS_DENIED)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=errors.NOT_FOUND)
