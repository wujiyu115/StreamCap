from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str


class RecordingCreate(BaseModel):
    url: str
    streamer_name: str = ""
    record_format: str = "TS"
    quality: str = "OD"
    segment_record: Optional[bool] = None
    segment_time: Optional[int] = None
    monitor_status: bool = True
    scheduled_recording: bool = False
    scheduled_start_time: Optional[str] = None
    monitor_hours: Optional[str] = None
    recording_dir: Optional[str] = None
    enabled_message_push: bool = False
    only_notify_no_record: bool = False
    flv_use_direct_download: Optional[bool] = None
    video_bitrate: Optional[int] = None
    pose_enabled: Optional[bool] = None


class RecordingUpdate(BaseModel):
    url: Optional[str] = None
    streamer_name: Optional[str] = None
    record_format: Optional[str] = None
    quality: Optional[str] = None
    segment_record: Optional[bool] = None
    segment_time: Optional[int] = None
    monitor_status: Optional[bool] = None
    scheduled_recording: Optional[bool] = None
    scheduled_start_time: Optional[str] = None
    monitor_hours: Optional[str] = None
    recording_dir: Optional[str] = None
    enabled_message_push: Optional[bool] = None
    only_notify_no_record: Optional[bool] = None
    flv_use_direct_download: Optional[bool] = None
    video_bitrate: Optional[int] = None
    pose_enabled: Optional[bool] = None


class BatchCreateRequest(BaseModel):
    """每行格式: 画质,URL,主播名（画质为 0-4 索引，对应 OD/UHD/HD/SD/LD）"""

    lines: list[str] = Field(default_factory=list)


class BatchDeleteRequest(BaseModel):
    ids: list[str]


class BatchMonitorRequest(BaseModel):
    ids: list[str]
    enabled: bool


class MonitorToggleRequest(BaseModel):
    enabled: bool


class ValidityCheckRequest(BaseModel):
    """直播间有效性检测；ids 为空 = 检测全部任务。

    limit 限制单次实际检测条数（分批防风控/防超时）；force+force_since
    为全量重检（checked_at 早于 force_since 的条目重检）。
    """
    ids: list[str] = Field(default_factory=list)
    force: bool = False
    force_since: float = 0
    limit: int = 30


class SettingsUpdate(BaseModel):
    user_settings: dict


class CookiesUpdate(BaseModel):
    cookies: dict


class AccountsUpdate(BaseModel):
    accounts: dict


class MediaBatchDeleteRequest(BaseModel):
    paths: list[str]


class MediaCleanRequest(BaseModel):
    path: str = ""
    max_bytes: int = Field(..., ge=0)


class PoseTaskSubmitRequest(BaseModel):
    paths: list[str]
    trigger: str = "manual"
    overrides: dict | None = None
