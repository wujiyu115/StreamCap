from __future__ import annotations

import os
import shutil

VIDEO_EXT = {"mp4", "webm", "mov", "mkv", "m4v", "ogv", "ts", "m2ts", "mts", "flv"}
IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "webp", "avif", "bmp"}
CLEAN_SKIP_SUFFIXES = ("_merged",)


class MediaPathError(PermissionError):
    """Raised when a requested path escapes the media root."""


def classify(name: str) -> str | None:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in VIDEO_EXT:
        return "video"
    if ext in IMAGE_EXT:
        return "image"
    return None


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def resolve_safe(root: str, rel: str) -> str:
    rel = (rel or "").strip().lstrip("/")
    if "\\" in rel or ".." in rel.split("/"):
        raise MediaPathError("invalid path")
    real_root = os.path.realpath(root)
    full = os.path.realpath(os.path.join(real_root, rel))
    if full != real_root and not full.startswith(real_root + os.sep):
        raise MediaPathError("out of root")
    return full


def media_root_path(root: str) -> str:
    return os.path.realpath(root)


def count_media(abspath: str) -> int:
    total = 0
    for _root, _dirs, files in os.walk(abspath):
        total += sum(1 for f in files if classify(f))
    return total


def stats(rel: str, root: str) -> dict:
    """递归统计媒体文件数与总大小。"""
    base = resolve_safe(root, rel)
    if not os.path.isdir(base):
        raise FileNotFoundError(rel)
    total_files = video_files = 0
    total_bytes = 0
    for _root, _dirs, files in os.walk(base):
        for f in files:
            kind = classify(f)
            if not kind:
                continue
            total_files += 1
            if kind == "video":
                video_files += 1
            total_bytes += os.path.getsize(os.path.join(_root, f))
    return {
        "total_files": total_files,
        "video_files": video_files,
        "total_bytes": total_bytes,
        "total_size": human_size(total_bytes),
    }


def delete_one(rel: str, root: str) -> None:
    """删除单个文件或目录（目录递归）。拒删根。"""
    full = resolve_safe(root, rel)
    if full == media_root_path(root):
        raise MediaPathError("cannot delete root")
    if os.path.isdir(full):
        shutil.rmtree(full)
    elif os.path.isfile(full):
        os.remove(full)
    else:
        raise FileNotFoundError(rel)


def delete_paths(paths: list[str], root: str) -> dict:
    """批量删除。逐个尝试，失败的收集到 failed，不中断其余。"""
    deleted, failed = 0, []
    for p in paths:
        try:
            delete_one(p, root)
            deleted += 1
        except Exception:
            failed.append(p)
    return {"deleted": deleted, "failed": failed}


def clean_small(rel: str, max_bytes: int, root: str) -> dict:
    """递归删除小于 max_bytes 的媒体文件，再删除因此变空的目录。

    带处理产物后缀（_merged）的文件跳过——它们是人体识别切割出的
    有效内容，即使小于阈值也不清理。
    """
    base = resolve_safe(root, rel)
    if not os.path.isdir(base):
        raise FileNotFoundError(rel)
    deleted_files = deleted_dirs = skipped_products = 0
    for walk_root, _dirs, files in os.walk(base, topdown=False):
        for f in files:
            p = os.path.join(walk_root, f)
            if not classify(f):
                continue
            if os.path.splitext(f)[0].endswith(CLEAN_SKIP_SUFFIXES):
                if os.path.getsize(p) < max_bytes:
                    skipped_products += 1
                continue
            if os.path.getsize(p) < max_bytes:
                os.remove(p)
                deleted_files += 1
        if walk_root != base and not os.listdir(walk_root):
            os.rmdir(walk_root)
            deleted_dirs += 1
    return {"deleted_files": deleted_files, "deleted_dirs": deleted_dirs, "skipped_products": skipped_products}


def list_dir(rel: str, root: str) -> dict:
    base = resolve_safe(root, rel)
    if not os.path.isdir(base):
        raise FileNotFoundError(rel)
    real_root = media_root_path(root)
    folders, media = [], []
    for entry in os.scandir(base):
        entry_rel = os.path.relpath(entry.path, real_root)
        if entry.is_dir():
            folders.append(
                {
                    "type": "folder",
                    "name": entry.name,
                    "rel_path": entry_rel,
                    "count": count_media(entry.path),
                    "mtime": int(entry.stat().st_mtime),
                }
            )
        elif entry.is_file():
            kind = classify(entry.name)
            if not kind:
                continue
            st = entry.stat()
            media.append(
                {
                    "type": kind,
                    "name": entry.name,
                    "rel_path": entry_rel,
                    "ext": entry.name.rsplit(".", 1)[-1].upper(),
                    "size": human_size(st.st_size),
                    "bytes": st.st_size,
                    "mtime": int(st.st_mtime),
                }
            )
    folders.sort(key=lambda x: x["name"].lower())
    media.sort(key=lambda x: x["name"].lower())
    norm = "" if rel in (None, "", ".") else rel.strip("/")
    return {"path": norm, "items": folders + media}
