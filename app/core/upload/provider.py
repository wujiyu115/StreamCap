from __future__ import annotations

from typing import Protocol

from .models import UploadRequest, UploadResult


class UploadProvider(Protocol):
    name: str

    def upload(self, request: UploadRequest, config: dict) -> UploadResult:
        """阻塞上传；由 UploadManager 的专用工作线程调用。"""
        ...
