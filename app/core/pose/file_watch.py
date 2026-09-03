"""文件就绪判定：识别视频文件是否仍在被写入（录制/转码中）。

事实判定替代旧的「mtime 距今 N 分钟」猜测：
1. 写句柄检查（fuser/lsof）：有进程持有该文件句柄 = 正在写入
2. 两轮 mtime 采样兜底（无 fuser/lsof 可用时）

自动触发的任务用 wait_until_ready 循环复查未就绪文件（就绪即处理，
无需用户配置任何等待时长）；手动提交用单次检查直接拒绝未就绪文件。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Callable

# 句柄检查工具可用哪个（fuser 优先：对单文件查询最轻）
_FUSER = shutil.which("fuser")
_LSOF = shutil.which("lsof")

# 两轮 mtime 采样间隔（秒）
MTIME_SAMPLE_INTERVAL = 2.0

# 防御性放弃：文件从未出现且持续这么久（转码彻底失败等异常），跳过该
# 文件避免任务永久挂起。正常场景 hook 触发时文件必然已存在。
STALE_ABANDON_SECONDS = 600


def _has_open_handle(path: str) -> bool | None:
    """有进程持有该文件句柄返回 True；确定无返回 False；无法判定返回 None。"""
    if _FUSER:
        try:
            # fuser -s: 静默探测，退出码 0=有进程使用，1=无
            r = subprocess.run(
                [_FUSER, "-s", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return r.returncode == 0
        except OSError:
            pass
    if _LSOF:
        try:
            r = subprocess.run(
                [_LSOF, "-t", "--", path],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return bool(r.stdout.strip())
        except OSError:
            pass
    return None


def _mtime(path: str) -> float | None:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def is_file_ready(path: str) -> bool:
    """单次就绪判定（不等待）。文件不存在直接未就绪。"""
    m = _mtime(path)
    if m is None:
        return False
    handle = _has_open_handle(path)
    if handle is True:
        return False
    if handle is False:
        return True
    # 无句柄工具：两轮 mtime 采样，变动即视为仍在写
    time.sleep(MTIME_SAMPLE_INTERVAL)
    return _mtime(path) == m


def wait_until_ready(
    videos: list[str],
    log,
    stop_check: Callable[[], bool] | None = None,
    on_pending: Callable[[list[str]], None] | None = None,
    poll_interval: float = 5.0,
) -> list[str]:
    """等待文件写完。就绪的返回处理，未就绪的每 poll_interval 秒复查。

    Returns:
        (ready, abandoned): 就绪文件列表 + 被放弃的文件列表
    """
    pending = list(videos)
    ready: list[str] = []
    abandoned: list[str] = []
    first_seen: dict[str, float] = {p: time.time() for p in pending}

    while pending:
        if stop_check is not None and stop_check():
            log.info("收到停止请求，中止等待文件")
            break

        for path in pending[:]:
            m = _mtime(path)
            if m is None:
                continue  # 尚不存在，保持等待
            handle = _has_open_handle(path)
            if handle is True:
                continue  # 仍被写入
            if handle is False:
                # 句柄已释放：录制/转码进程已结束，文件完整
                pending.remove(path)
                ready.append(path)
                continue
            # 无句柄工具：两轮 mtime 采样
            time.sleep(MTIME_SAMPLE_INTERVAL)
            if _mtime(path) == m:
                pending.remove(path)
                ready.append(path)

        if not pending:
            break

        # 防御性放弃：文件迟迟不出现（写进程崩溃/转码彻底失败）
        now = time.time()
        for path in pending[:]:
            if not os.path.exists(path) and now - first_seen[path] > STALE_ABANDON_SECONDS:
                log.warning(f"文件长时间未出现，放弃: {path}")
                pending.remove(path)
                abandoned.append(path)

        if on_pending is not None and pending:
            on_pending([os.path.basename(p) for p in pending])

        # 睡眠可被停止信号提前打断
        deadline = time.time() + poll_interval
        while time.time() < deadline:
            if stop_check is not None and stop_check():
                break
            time.sleep(0.5)

    return ready, abandoned
