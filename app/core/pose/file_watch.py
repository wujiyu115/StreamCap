"""文件就绪判定：识别视频文件是否仍在被写入（录制/转码中）。

事实判定替代旧的「mtime 距今 N 分钟」猜测，两个信号都要过：
1. 写句柄检查（fuser/lsof）：有进程持有该文件句柄 = 正在写入，直接否
2. 两轮 mtime 采样：变动 = 仍在写

句柄检查只用来「否决」，不用来「放行」——它只看得到本容器 PID 命名空间里的
进程，从 SMB 之类外部途径拷进来的文件看不到任何句柄，光凭这一条会把写了一半
的文件当成就绪。反过来 mtime 也不能单独用：录制中途卡住（断流/转码停顿）时
文件静止但 ffmpeg 还攥着句柄，这时得靠句柄检查兜住。
代价是就绪判定至少要 MTIME_SAMPLE_INTERVAL 秒——所以批量判定共享这一轮采样
（见 filter_ready），别逐个文件调。

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


def filter_ready(paths: list[str]) -> tuple[list[str], list[str]]:
    """批量单次就绪判定（不等待），返回 (ready, not_ready)，保持入参顺序。

    关键点：无句柄工具时的两轮 mtime 采样**只睡一轮**，所有待采样文件共享这
    MTIME_SAMPLE_INTERVAL 秒。逐个文件调 is_file_ready 是 O(N) 个 sleep，
    提交一个目录（几十个文件）就要睡几十秒。
    """
    verdict: dict[str, bool] = {}
    sampling: dict[str, float] = {}
    for path in paths:
        if path in verdict or path in sampling:
            continue
        m = _mtime(path)
        if m is None:
            verdict[path] = False  # 文件不存在直接未就绪
        elif _has_open_handle(path) is True:
            verdict[path] = False  # 有进程持有句柄 = 正在写
        else:
            sampling[path] = m  # 句柄已释放或无从判定，都再用 mtime 确认一轮

    if sampling:
        time.sleep(MTIME_SAMPLE_INTERVAL)
        for path, m in sampling.items():
            verdict[path] = _mtime(path) == m  # mtime 变动即视为仍在写

    ready = [p for p in paths if verdict.get(p)]
    not_ready = [p for p in paths if not verdict.get(p)]
    return ready, not_ready


def is_file_ready(path: str) -> bool:
    """单次就绪判定（不等待）。文件不存在直接未就绪。"""
    return bool(filter_ready([path])[0])


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

        # 句柄已释放（录制/转码进程已结束）或 mtime 两轮不变 = 文件完整
        newly_ready, _ = filter_ready(pending)
        for path in newly_ready:
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
