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

# CLIP 零样本服装分类的 prompt 模板：正类=紧身下装（长裤与短裤都算），
# 负类=宽松及其他下装。负类的短裤必须带 loose/baggy 限定词——裸 "shorts"
# 会同时吸走紧身短裤帧（CLIP 对服装类型词比对剪裁修饰词敏感）。
DEFAULT_POSITIVE_PROMPTS: list[str] = [
    "a photo of a person wearing tight leggings",
    "a photo of a person wearing yoga pants",
    "a photo of a person wearing skin-tight athletic pants",
    "a photo of a person wearing tight-fitting pants",
    "a photo of a person wearing tight-fitting shorts",
    "a photo of a person wearing skin-tight athletic shorts",
]
DEFAULT_NEGATIVE_PROMPTS: list[str] = [
    "a photo of a person wearing loose pants",
    "a photo of a person wearing baggy sweatpants",
    "a photo of a person wearing jeans",
    "a photo of a person wearing a skirt",
    "a photo of a person wearing loose shorts",
    "a photo of a person wearing baggy shorts",
    "a photo of a person wearing a dress",
    "a photo of bare legs",
]

# 参数默认值也用于 default_settings.json 的 pose_detection 段，
# 两处保持一致（设置读取时 user→default 回退）。
DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "frame_seconds": 10.0,
    "imgsz": 416,
    "batch_size": 8,
    "inference_threads": 0,
    "confidence_threshold": 0.5,
    "enable_pose_detection": True,
    "pose_filter": "standing",
    "standing_angle": 45.0,
    "person_min_ratio": 0.2,
    "merge_threshold_seconds": 12.0,
    "min_segment_seconds": 30.0,
    "merge_clips": True,
    "delete_original_video": True,
    "move_output_to_input": True,
    "video_output_dir": "pose_output",
    "merged_suffix": "_merged",
    "model_path": DEFAULT_DETECTION_MODEL,
    "pose_model_path": DEFAULT_POSE_MODEL,
    "decode_backend": "auto",
    "clip_filter_enabled": False,
    # openai 权重必须配 quickgelu 架构变体，否则激活函数不匹配（有 UserWarning、精度下降）
    "clip_model_name": "ViT-B-32-quickgelu",
    "clip_pretrained": "openai",
    "clip_positive_prompts": list(DEFAULT_POSITIVE_PROMPTS),
    "clip_negative_prompts": list(DEFAULT_NEGATIVE_PROMPTS),
    "clip_min_positive_ratio": 0.5,
    "clip_sample_seconds": 30.0,
    # 分类明细报告（json/html/缩略图）默认关闭——只用于人工核对判定效果，
    # 生产不需要；开启时落在任务目录 clip_reports/，随任务目录 7 天自动清理
    "clip_save_reports": False,
}


@dataclass
class PoseParams:
    frame_seconds: float = 10.0
    imgsz: int = 416
    batch_size: int = 8
    inference_threads: int = 0
    confidence_threshold: float = 0.5
    enable_pose_detection: bool = True
    pose_filter: str = "standing"
    standing_angle: float = 45.0
    person_min_ratio: float = 0.2
    merge_threshold_seconds: float = 12.0
    min_segment_seconds: float = 30.0
    merge_clips: bool = True
    delete_original_video: bool = True
    move_output_to_input: bool = True
    video_output_dir: str = "pose_output"
    merged_suffix: str = "_merged"
    model_path: str = field(default_factory=lambda: DEFAULT_DETECTION_MODEL)
    pose_model_path: str = field(default_factory=lambda: DEFAULT_POSE_MODEL)
    decode_backend: str = "auto"
    clip_filter_enabled: bool = False
    clip_model_name: str = "ViT-B-32-quickgelu"
    clip_pretrained: str = "openai"
    clip_positive_prompts: list[str] = field(
        default_factory=lambda: list(DEFAULT_POSITIVE_PROMPTS)
    )
    clip_negative_prompts: list[str] = field(
        default_factory=lambda: list(DEFAULT_NEGATIVE_PROMPTS)
    )
    clip_min_positive_ratio: float = 0.5
    clip_sample_seconds: float = 30.0
    clip_save_reports: bool = False

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
            # prompt 清空（UI 里删光）回退默认组，全空组会导致分类完全失真
            if key in ("clip_positive_prompts", "clip_negative_prompts") and not value:
                value = list(default)
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
