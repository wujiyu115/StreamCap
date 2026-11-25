"""人体识别「一帧都没切出来」时不能删原视频。

2026-11 复盘：`process_video_file` 里 `if self.delete_original_video: safe_remove_file(video_path)`
是无条件的。全片无正例 / CLIP 整段拒绝 / 用户中途停止都会让 `clip_paths` 为空，原视频仍然被删，
产物是 0 字节——11 月的录制分析里就有 59 条这样被清零（最大的 2.3GB / 2.4 小时）。
典型事故：兔子 2026-11-25 23:07~23:52 录了 45 分钟 306MB，识别没切出片段，原视频被删光。
"""
from app.core.pose.pose_params import PoseParams
from app.core.pose.video_processor import VideoProcessor


class _Detector:
    """只需要 `.model` 属性（真检测被 process_video 替身绕过）。"""

    model = None
    batch_size = 16


def make_processor(tmp_path, **overrides):
    params = PoseParams(**overrides) if overrides else PoseParams()
    params.delete_original_video = True
    return VideoProcessor(detector=_Detector(), params=params, media_root=str(tmp_path))


def fake_run(processor, merged_segments, clip_paths):
    """把「检测 + 切割」两步替换成固定结果，只留被删/保留的判断逻辑。"""
    processor.process_video = lambda *a, **kw: (100, 100, [], [], [])   # 无人 -> 空区间
    processor.merge_segments = lambda *a, **kw: merged_segments
    processor.clip_video = lambda *a, **kw: list(clip_paths)


def test_no_clip_keeps_original(tmp_path):
    video = tmp_path / "兔子_2026-11-25_23-07-09_000.mp4"
    video.write_bytes(b"x" * 2048)
    processor = make_processor(tmp_path)
    fake_run(processor, [], [])

    processor.process_video_file(str(video))

    assert video.exists(), "没切出任何片段时原视频必须还在"


def test_clip_filter_rejected_keeps_original(tmp_path):
    """CLIP 整段拒绝 → skip_clipping → clip_paths 为空，同样不能删。"""
    video = tmp_path / "clip_reject.mp4"
    video.write_bytes(b"x" * 1024)
    processor = make_processor(tmp_path)
    fake_run(processor, [{"start": 0.0, "end": 10.0}], [])

    processor.process_video_file(str(video))

    assert video.exists()


def test_with_clips_deletes_original(tmp_path):
    """正常情况：切出了片段，原视频按开关删掉（不改变既有行为）。"""
    video = tmp_path / "normal.mp4"
    video.write_bytes(b"x" * 1024)
    clip = tmp_path / "normal_merged.mp4"
    processor = make_processor(tmp_path)
    fake_run(processor, [{"start": 0.0, "end": 10.0}], [str(clip)])
    clip.write_bytes(b"y" * 512)

    result = processor.process_video_file(str(video))

    assert not video.exists()
    assert result[5] > 0        # output_bytes
    assert result[6] == 1024    # deleted_bytes = 原视频大小
