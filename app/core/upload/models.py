from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class UploadAuthError(RuntimeError):
    """渠道凭据缺失、过期或被平台拒绝；重试不会自行恢复。"""


@dataclass(frozen=True)
class UploadRequest:
    """与具体上传渠道无关的一次投稿请求。"""

    job_id: str
    files: list[str]
    title: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UploadResult:
    provider: str
    remote_id: str | None = None
    message: str = ""
