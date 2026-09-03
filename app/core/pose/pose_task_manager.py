"""人体识别任务管理器：运行在 FastAPI 主进程内（不 import torch）。

负责拉起/监控/停止 task_runner 子进程。状态通过 <task_dir>/state.json 交换，
日志通过 <task_dir>/task.log 收集（stderr 重定向）。移植自
video_pose/app/task_manager.py，改造点：
- 配置来源改为 user_settings 的 pose_detection 段（JSON spec 传给子进程）；
- 手动任务忙时 409，自动任务（录制完成钩子）进入等待队列串行执行。
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ...utils.logger import logger

LOG_CHUNK_LIMIT = 64 * 1024
MAX_QUEUE = 20


class TaskBusyError(Exception):
    """已有任务在运行"""


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _atomic_write_json(path: str, payload: dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, path)


class PoseTaskManager:
    """单运行 + 等待队列。线程安全（FastAPI 多 worker 请求 + 队列消费线程并发访问）。"""

    def __init__(self, run_path: str):
        self.tasks_root = os.path.join(run_path, "logs", "pose_tasks")
        os.makedirs(self.tasks_root, exist_ok=True)

        self._lock = threading.Lock()
        self._task_id: Optional[str] = None
        self._task_dir: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None
        self._orphan_pid: Optional[int] = None
        self._queue: list[dict[str, Any]] = []
        self._history: list[dict[str, Any]] = []
        self._history_lock = threading.Lock()
        self._stop_worker = False
        self._worker: Optional[threading.Thread] = None

        self._adopt_orphans()
        self._prune_old_tasks()
        self._start_worker()

    # ── 生命周期 ────────────────────────────────────────────

    def _adopt_orphans(self):
        """服务重启后接管仍在运行的孤儿任务，其余标记为中断。"""
        if not os.path.isdir(self.tasks_root):
            return
        for name in sorted(os.listdir(self.tasks_root), reverse=True):
            state_path = os.path.join(self.tasks_root, name, "state.json")
            if not os.path.isfile(state_path):
                continue
            try:
                with open(state_path, encoding="utf-8") as f:
                    state = json.load(f)
            except (OSError, ValueError):
                continue
            if state.get("status") != "running":
                continue
            pid = state.get("pid")
            if pid and _pid_alive(int(pid)):
                with self._lock:
                    self._task_id = name
                    self._task_dir = os.path.dirname(state_path)
                    self._proc = None
                    self._orphan_pid = int(pid)
                logger.info(f"接管人体识别孤儿任务: {name} (pid={pid})")
                return
            state["status"] = "failed"
            state["message"] = "服务重启时任务进程已不在，标记为中断"
            state["finished_at"] = datetime.now().isoformat(timespec="seconds")
            _atomic_write_json(state_path, state)

    def _start_worker(self):
        def worker():
            last_prune = 0.0
            while not self._stop_worker:
                next_spec = None
                with self._lock:
                    if self._queue and not self._is_running_locked():
                        next_spec = self._queue.pop(0)
                if next_spec is None:
                    # 每小时清一次过期任务目录
                    if time.time() - last_prune > 3600:
                        last_prune = time.time()
                        self._prune_old_tasks()
                    time.sleep(2.0)
                    continue
                try:
                    self._spawn(next_spec)
                except Exception as e:
                    logger.error(f"启动人体识别任务失败: {e}")

        self._worker = threading.Thread(target=worker, name="PoseTaskWorker", daemon=True)
        self._worker.start()

    def _prune_old_tasks(self, max_age_days: int = 7, keep_recent: int = 20) -> None:
        """删除超过 max_age_days 的任务目录；无论多老始终保留最近 keep_recent 个。"""
        try:
            names = sorted(
                n for n in os.listdir(self.tasks_root)
                if n.startswith("task_") and os.path.isdir(os.path.join(self.tasks_root, n))
            )
            cutoff = datetime.now().timestamp() - max_age_days * 86400
            for name in names[:-keep_recent] if keep_recent else names:
                state_path = os.path.join(self.tasks_root, name, "state.json")
                try:
                    with open(state_path, encoding="utf-8") as f:
                        state = json.load(f)
                except (OSError, ValueError):
                    continue
                if state.get("status") == "running":
                    continue
                finished = state.get("finished_at")
                if not finished:
                    continue
                try:
                    finished_ts = datetime.fromisoformat(finished).timestamp()
                except ValueError:
                    continue
                if finished_ts < cutoff:
                    shutil.rmtree(os.path.join(self.tasks_root, name), ignore_errors=True)
                    logger.info(f"清理过期人体识别任务目录: {name}")
        except Exception as e:
            logger.warning(f"清理人体识别任务目录失败: {e}")

    def shutdown(self) -> None:
        self._stop_worker = True
        try:
            self.stop()
        except TaskBusyError:
            pass

    # ── 提交 ────────────────────────────────────────────────

    def submit(
        self,
        videos: list[str],
        media_root: str,
        params: dict[str, Any],
        trigger: str = "manual",
        wait_file: bool = False,
    ) -> dict[str, Any]:
        """提交任务。manual 触发且忙时抛 TaskBusyError；auto 触发进队列。"""
        videos = [os.path.abspath(v) for v in videos if os.path.isabs(v) or True]
        spec = {
            "params": params,
            "videos": [os.path.abspath(v) for v in videos],
            "media_root": media_root,
            "wait_file": wait_file,
            "trigger": trigger,
        }

        with self._lock:
            if self._is_running_locked():
                if trigger == "manual":
                    raise TaskBusyError("已有任务在运行，请先停止或等待完成")
                if len(self._queue) >= MAX_QUEUE:
                    logger.warning("人体识别等待队列已满，丢弃任务")
                    raise TaskBusyError("等待队列已满")
                self._queue.append(spec)
                return {"task_id": None, "status": "queued"}

        task_id = self._spawn(spec)
        return {"task_id": task_id, "status": "running"}

    def _spawn(self, spec: dict[str, Any]) -> str:
        task_id = datetime.now().strftime("task_%Y%m%d_%H%M%S")
        task_dir = os.path.join(self.tasks_root, task_id)
        os.makedirs(task_dir, exist_ok=True)

        spec = dict(spec)
        spec["task_dir"] = task_dir
        spec_path = os.path.join(task_dir, "spec.json")
        with open(spec_path, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False, indent=2)

        log_path = os.path.join(task_dir, "task.log")
        log_file = open(log_path, "w", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "app.core.pose.task_runner", "--spec", spec_path],
                cwd=str(Path(__file__).resolve().parents[3]),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        finally:
            log_file.close()

        with self._lock:
            self._task_id = task_id
            self._task_dir = task_dir
            self._proc = proc
            self._orphan_pid = None

        _atomic_write_json(
            os.path.join(task_dir, "state.json"),
            {
                "status": "running",
                "state": "starting",
                "pid": proc.pid,
                "trigger": spec.get("trigger", "manual"),
                "videos": spec.get("videos", []),
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "message": "任务子进程启动中…",
            },
        )
        logger.info(f"人体识别任务已启动: {task_id} (pid={proc.pid}, trigger={spec.get('trigger')})")
        return task_id

    # ── 停止 ────────────────────────────────────────────────

    def stop(self) -> dict[str, Any]:
        snap = self.snapshot()
        if snap is None or snap.get("status") != "running":
            raise TaskBusyError("没有正在运行的任务")

        with self._lock:
            pid = self._orphan_pid if self._proc is None else self._proc.pid
            proc = self._proc
            task_dir = self._task_dir

        log_path = os.path.join(task_dir or "", "task.log")
        if pid and _pid_alive(pid):
            # 子进程刚拉起、还没写日志说明 SIGTERM 处理器可能尚未装好
            deadline = time.time() + 3.0
            while time.time() < deadline and (not task_dir or not os.path.getsize(log_path)):
                time.sleep(0.1)
            if _pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

        if proc is not None:
            try:
                proc.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        elif pid:
            deadline = time.time() + 10.0
            while time.time() < deadline and _pid_alive(pid):
                time.sleep(0.2)
            if _pid_alive(pid):
                os.kill(pid, signal.SIGKILL)

        return self.snapshot() or {"status": "stopping"}

    # ── 状态 / 日志 ─────────────────────────────────────────

    def _is_running_locked(self) -> bool:
        state_path = (
            os.path.join(self._task_dir, "state.json") if self._task_dir else None
        )
        if state_path is None or not os.path.isfile(state_path):
            return False
        try:
            with open(state_path, encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError):
            return False
        if state.get("status") != "running":
            return False
        return self._process_alive_locked()

    def _process_alive_locked(self) -> bool:
        if self._proc is not None:
            return self._proc.poll() is None
        return bool(self._orphan_pid and _pid_alive(self._orphan_pid))

    def snapshot(self) -> Optional[dict[str, Any]]:
        """当前任务快照（综合子进程存活状态与 state.json）。"""
        with self._lock:
            state_path = (
                os.path.join(self._task_dir, "state.json") if self._task_dir else None
            )
            task_id = self._task_id
            task_dir = self._task_dir
            proc = self._proc
            orphan_pid = self._orphan_pid
            queue_len = len(self._queue)

        if state_path is None or not os.path.isfile(state_path):
            return None

        try:
            with open(state_path, encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError):
            return None

        if state.get("status") == "running":
            alive = (
                proc.poll() is None
                if proc is not None
                else bool(orphan_pid and _pid_alive(orphan_pid))
            )
            if not alive:
                state["status"] = "failed"
                state["message"] = "任务进程异常退出（未写终态）"
                state["finished_at"] = datetime.now().isoformat(timespec="seconds")
                _atomic_write_json(state_path, state)

        state["task_id"] = task_id
        state["task_dir"] = task_dir
        state["queue_length"] = queue_len
        log_path = os.path.join(task_dir or "", "task.log")
        state["log_size"] = os.path.getsize(log_path) if os.path.isfile(log_path) else 0
        return state

    def list_tasks(self) -> list[dict[str, Any]]:
        """当前任务 + 历史任务列表（按时间倒序，最近 20 条）。"""
        result: list[dict[str, Any]] = []
        current = self.snapshot()
        if current is not None:
            result.append(current)

        if not os.path.isdir(self.tasks_root):
            return result

        current_dir = current.get("task_dir") if current else None
        for name in sorted(os.listdir(self.tasks_root), reverse=True):
            task_dir = os.path.join(self.tasks_root, name)
            if task_dir == current_dir:
                continue
            state_path = os.path.join(task_dir, "state.json")
            if not os.path.isfile(state_path):
                continue
            try:
                with open(state_path, encoding="utf-8") as f:
                    state = json.load(f)
            except (OSError, ValueError):
                continue
            state["task_id"] = name
            state["task_dir"] = task_dir
            result.append(state)
            if len(result) >= 20:
                break

        return result

    def read_log(self, task_id: str, offset: int) -> tuple[str, int]:
        """从 offset（字节）起读取任务日志增量。"""
        task_dir = os.path.join(self.tasks_root, task_id)
        log_path = os.path.join(task_dir, "task.log")
        if not os.path.isfile(log_path):
            return "", max(offset, 0)
        size = os.path.getsize(log_path)
        if offset < 0 or offset > size:
            offset = 0
        length = min(size - offset, LOG_CHUNK_LIMIT)
        with open(log_path, "rb") as f:
            f.seek(offset)
            chunk = f.read(length)
        return chunk.decode("utf-8", errors="replace"), offset + length
