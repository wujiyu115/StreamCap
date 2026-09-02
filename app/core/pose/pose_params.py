"""人体识别任务的参数模型。

来源是 user_settings.json 的 ``pose_detection`` 段；任务提交时序列化为
JSON spec 传给子进程（app.core.pose.task_runner），子进程不读任何全局配置。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MODELS_DIR = Path(__file__).parent / "models"
DEFAULT_DETECTION_MODEL = str(MODELS_DIR / "yolov8n.pt")
DEFAULT_POSE_MODEL = str(MODELS_DIR / "yolov8n-pose.pt")

POSE_FILTER_OPTIONS = ("none", "standing", "sitting")
DECODE_BACKEND_OPTIONS = ("auto", "pyav", "opencv")

# 参数默认值也用于 default_settings.json 的 pose_detection 段，
# 两处保持一致（设置读取时 user→default 回退）。
DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "frame_seconds": 10.0,
    "imgsz": 416,
    "batch_size": 8,
    "confidence_threshold": 0.5,
    "enable_pose_detection": True,
    "pose_filter": "none",
    "standing_angle": 45.0,
    "person_min_ratio": 0.2,
    "merge_threshold_seconds": 12.0,
    "min_segment_seconds": 30.0,
    "merge_clips": True,
    "delete_original_video": True,
    "move_output_to_input": True,
    "video_output_dir": "pose_output",
    "merged_suffix": "_merged",
    "min_file_age_minutes": 60,
    "wait_file_timeout_minutes": 15,
    "model_path": DEFAULT_DETECTION_MODEL,
    "pose_model_path": DEFAULT_POSE_MODEL,
    "decode_backend": "auto",
}


@dataclass
class PoseParams:
    frame_seconds: float = 10.0
    imgsz: int = 416
    batch_size: int = 8
    confidence_threshold: float = 0.5
    enable_pose_detection: bool = True
    pose_filter: str = "none"
    standing_angle: float = 45.0
    person_min_ratio: float = 0.2
    merge_threshold_seconds: float = 12.0
    min_segment_seconds: float = 30.0
    merge_clips: bool = True
    delete_original_video: bool = True
    move_output_to_input: bool = True
    video_output_dir: str = "pose_output"
    merged_suffix: str = "_merged"
    min_file_age_minutes: float = 60
    wait_file_timeout_minutes: float = 15
    model_path: str = field(default_factory=lambda: DEFAULT_DETECTION_MODEL)
    pose_model_path: str = field(default_factory=lambda: DEFAULT_POSE_MODEL)
    decode_backend: str = "auto"

    @classmethod
    def from_user_config(cls, config: dict[str, Any] | None) -> PoseParams:
        """从 user_settings['pose_detection'] 构造（缺省回退 DEFAULTS）。"""
        section = config or {}
        kwargs: dict[str, Any] = {}
        for key, default in DEFAULTS.items():
            if key == "enabled":
                continue
            value = section.get(key, default)
            if key in ("pose_filter", "decode_backend") and value not in (
                POSE_FILTER_OPTIONS if key == "pose_filter" else DECODE_BACKEND_OPTIONS
            ):
                value = default
            kwargs[key] = value
        try:
            return cls(**kwargs)
        except TypeError:
            return cls()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_pose_enabled(user_config: dict[str, Any], recording_pose_enabled: bool | None) -> bool:
    """全局开关 + 任务级覆盖（None=跟随全局）判定是否自动处理。"""
    section = user_config.get("pose_detection") or {}
    enabled = bool(section.get("enabled", DEFAULTS["enabled"]))
    if recording_pose_enabled is None:
        return enabled
    return recording_pose_enabled
