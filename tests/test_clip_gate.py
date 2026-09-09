"""视频处理闸门集成测试：CLIP 判定对切割/删除路径的影响。

fake detector/model（无 torch 依赖），跑法:
.venv/bin/python -m pytest tests/test_clip_gate.py -v
"""

import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.pose import video_processor as vp
from app.core.pose.pose_params import PoseParams


def _make_video(tmp_path, name="v.mp4", frames=300, fps=30):
    """合成一段小 mp4（lavfi testsrc），返回路径。"""
    import ffmpeg

    path = str(tmp_path / name)
    (
        ffmpeg.input(
            "testsrc=size=320x240:rate=%d" % fps,
            format="lavfi",
        )
        .output(path, t=frames / fps, pix_fmt="yuv420p")
        .overwrite_output()
        .run(capture_stdout=True, capture_stderr=True)
    )
    return path


class _FakeClipFilter:
    """可编程的 CLIP 分类器替身。"""

    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.calls = []

    def classify(self, crop_bgr):
        self.calls.append(crop_bgr.shape)
        v = self.verdicts.pop(0) if self.verdicts else {"is_positive": True}
        return {
            "pos_prob": 0.9 if v["is_positive"] else 0.1,
            "is_positive": v["is_positive"],
            "prompt_sims": {},
        }


def _fake_result(keypoints_conf=0.9, with_kp=True):
    """冒充 ultralytics 的单帧结果：一个人体框 + 关键点。"""
    kp = np.zeros((17, 3), dtype=float)
    for idx in (11, 12, 13, 14, 15, 16):
        kp[idx] = (100 + idx, 100 + idx * 10, keypoints_conf)
    boxes = SimpleNamespace(
        xyxy=SimpleNamespace(
            cpu=lambda: SimpleNamespace(
                numpy=lambda: np.array([[80, 60, 240, 220]], dtype=float)
            )
        )
    )
    result = SimpleNamespace(
        orig_img=np.zeros((240, 320, 3), dtype=np.uint8),
        boxes=boxes,
        keypoints=SimpleNamespace(
            data=SimpleNamespace(
                cpu=lambda: SimpleNamespace(numpy=lambda: kp[None, :, :])
            )
        )
        if with_kp
        else None,
        summary=lambda: [{"name": "person"}],
    )
    return result


def _build_processor(tmp_path, clip_enabled, fake_filter, params_overrides=None):
    overrides = {
        "clip_filter_enabled": clip_enabled,
        "delete_original_video": False,
        "move_output_to_input": False,
        "min_segment_seconds": 1.0,
        "merge_threshold_seconds": 5.0,
        "frame_seconds": 1.0,
        "imgsz": 64,
        "batch_size": 4,
    }
    overrides.update(params_overrides or {})
    params = PoseParams.from_user_config(overrides)
    detector = SimpleNamespace(
        model=lambda *a, **kw: [_fake_result()],
        batch_size=4,
        imgsz=64,
        conf_threshold=0.5,
        check_person=lambda result: (
            True,
            result.orig_img,
            0.5,
            np.array([80, 60, 240, 220], dtype=float),
            result.keypoints.data.cpu().numpy()[0] if result.keypoints else None,
        ),
    )
    processor = vp.VideoProcessor(
        detector, params, media_root=str(tmp_path), report_dir=str(tmp_path / "reports")
    )
    processor._clip_filter = fake_filter
    processor._clip_state = "ready" if fake_filter else "off"
    return processor


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    return _make_video(tmp_path_factory.mktemp("videos"), frames=180, fps=30)


class TestClipGate:
    def test_disabled_keeps_legacy_behavior(self, sample_video, tmp_path):
        """闸门关闭时返回 disabled 判定，切割照常发生。"""
        processor = _build_processor(tmp_path, clip_enabled=False, fake_filter=None)
        ret = processor.process_video_file(sample_video)
        verdict = ret[7]
        assert verdict["verdict"] == "disabled"
        assert ret[4] >= 1  # clips
        assert os.path.exists(sample_video)  # delete_original_video=False

    def test_reject_skips_clipping_entirely(self, sample_video, tmp_path):
        """判定 reject：不切割不合并（clips=0），原视频保留（delete=False）。"""
        fake = _FakeClipFilter([{"is_positive": False}, {"is_positive": False}])
        processor = _build_processor(
            tmp_path, clip_enabled=True, fake_filter=fake,
            params_overrides={"clip_sample_seconds": 1.0},
        )
        ret = processor.process_video_file(sample_video)
        verdict = ret[7]
        assert verdict["verdict"] == "reject"
        assert ret[4] == 0  # clips
        assert os.path.exists(sample_video)

    def test_pass_clips_normally(self, sample_video, tmp_path):
        fake = _FakeClipFilter([{"is_positive": True}, {"is_positive": True}])
        processor = _build_processor(
            tmp_path, clip_enabled=True, fake_filter=fake,
            params_overrides={"clip_sample_seconds": 1.0},
        )
        ret = processor.process_video_file(sample_video)
        assert ret[7]["verdict"] == "pass"
        assert ret[4] >= 1  # clips

    def test_sample_interval_limits_classifications(self, sample_video, tmp_path):
        """clip_sample_seconds 节流：6 秒视频、30s 间隔只应分类 1 帧。"""
        fake = _FakeClipFilter([])
        processor = _build_processor(
            tmp_path, clip_enabled=True, fake_filter=fake,
            params_overrides={"clip_sample_seconds": 30.0},
        )
        processor.process_video_file(sample_video)
        assert len(fake.calls) == 1

    def test_classify_failure_fails_open(self, sample_video, tmp_path):
        """分类器抛异常 → 该帧跳过；全部失败 → insufficient → 照常切割。"""

        class Broken:
            def classify(self, crop):
                raise RuntimeError("boom")

        processor = _build_processor(
            tmp_path, clip_enabled=True, fake_filter=Broken(),
            params_overrides={"clip_sample_seconds": 1.0},
        )
        ret = processor.process_video_file(sample_video)
        assert ret[7]["verdict"] == "insufficient"
        assert ret[4] >= 1  # clips 照常

    def test_report_written_with_crops(self, sample_video, tmp_path):
        fake = _FakeClipFilter([{"is_positive": True}, {"is_positive": False}])
        reports = tmp_path / "reports"
        processor = _build_processor(
            tmp_path, clip_enabled=True, fake_filter=fake,
            params_overrides={"clip_sample_seconds": 1.0},
        )
        assert processor._report is not None
        processor.process_video_file(sample_video)
        entries = list(reports.glob("*/report.json"))
        assert len(entries) == 1
        import json

        with open(entries[0], encoding="utf-8") as f:
            report = json.load(f)
        assert report["verdict"]["classified"] == 2
        assert len(report["frames"]) == 2
        html_path = entries[0].parent / "report.html"
        assert html_path.exists()
