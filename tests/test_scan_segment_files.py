"""分段文件扫描：只扫单层、按前缀取、按 mtime 过滤。

替换掉原先的递归 os.walk——录制目录会随时间累积上万个历史文件，递归遍历随目录
线性变慢，还会把子目录里的识别/转码产物误算进本次录制的产出。
"""
import os

from app.utils.utils import scan_segment_files

PREFIX = "主播_标题_2026-09-07_20-00-00"


def _touch(path: str, size: int = 1, mtime: float | None = None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_picks_matching_prefix_only(tmp_path):
    d = str(tmp_path)
    _touch(os.path.join(d, f"{PREFIX}_001.ts"), 10)
    _touch(os.path.join(d, f"{PREFIX}_002.ts"), 20)
    _touch(os.path.join(d, "别的主播_001.ts"), 999)

    result = scan_segment_files(d, PREFIX)

    assert [os.path.basename(p) for p, _s, _m in result] == [f"{PREFIX}_001.ts", f"{PREFIX}_002.ts"]
    assert sum(s for _p, s, _m in result) == 30


def test_does_not_descend_into_subdirectories(tmp_path):
    """识别产物落在子目录，递归扫会把它重复计成本次录制的产出。"""
    d = str(tmp_path)
    _touch(os.path.join(d, f"{PREFIX}_001.ts"), 10)
    _touch(os.path.join(d, "pose_output", f"{PREFIX}_001_merged.mp4"), 500)

    result = scan_segment_files(d, PREFIX)

    assert len(result) == 1
    assert sum(s for _p, s, _m in result) == 10


def test_since_ts_excludes_earlier_runs(tmp_path):
    """自定义文件名模板不含 {time} 时前缀会退化，历次录制共享同一前缀。"""
    d = str(tmp_path)
    _touch(os.path.join(d, f"{PREFIX}_001.ts"), 100, mtime=1_000_000)
    _touch(os.path.join(d, f"{PREFIX}_002.ts"), 7, mtime=2_000_000)

    result = scan_segment_files(d, PREFIX, since_ts=1_500_000)

    assert [os.path.basename(p) for p, _s, _m in result] == [f"{PREFIX}_002.ts"]
    assert sum(s for _p, s, _m in result) == 7


def test_since_ts_boundary_is_inclusive(tmp_path):
    d = str(tmp_path)
    _touch(os.path.join(d, f"{PREFIX}_001.ts"), 5, mtime=1_500_000)

    assert len(scan_segment_files(d, PREFIX, since_ts=1_500_000)) == 1


def test_missing_directory_returns_empty(tmp_path):
    assert scan_segment_files(os.path.join(str(tmp_path), "nope"), PREFIX) == []


def test_directories_matching_prefix_are_skipped(tmp_path):
    d = str(tmp_path)
    os.makedirs(os.path.join(d, f"{PREFIX}_dir"))
    _touch(os.path.join(d, f"{PREFIX}_001.ts"), 3)

    result = scan_segment_files(d, PREFIX)

    assert [os.path.basename(p) for p, _s, _m in result] == [f"{PREFIX}_001.ts"]


def test_results_are_sorted_by_path(tmp_path):
    d = str(tmp_path)
    for idx in (3, 1, 2):
        _touch(os.path.join(d, f"{PREFIX}_00{idx}.ts"), idx)

    result = scan_segment_files(d, PREFIX)

    assert [os.path.basename(p) for p, _s, _m in result] == [
        f"{PREFIX}_001.ts",
        f"{PREFIX}_002.ts",
        f"{PREFIX}_003.ts",
    ]
