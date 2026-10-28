from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from ...messages.message_pusher import MessagePusher
from ...utils.logger import logger
from .models import UploadAuthError, UploadRateLimited, UploadRequest
from .providers import BilibiliProvider

_RETRY_DELAYS = (60, 300, 900, 3600)

# 风控冷却默认值：B 站「上传视频过快」实测不手动过验证能撑 3~4 天，6h 起步 + 翻倍封顶 24h
_DEFAULT_COOLDOWN_SECONDS = 21600
_DEFAULT_MAX_COOLDOWN_SECONDS = 86400

# 投递节奏门：两个投稿之间的最小间隔（秒）。
# 依据：2026-10-28 生产完全没有间隔时，10:40 一分钟内投了 4 单、13 分钟 11 单即触发 406；
# 社区（biliup#1362）一天连续投 9 单开始回 21564。30 分钟/条 = 每小时 2 单，
# 密度比出事时低一个量级，又不会让积压永远排不完。想更激进/更保守改 uploads.min_interval_seconds。
_DEFAULT_MIN_INTERVAL_SECONDS = 1800

# 「任务还没结束」的状态：同一文件再次提交时不能重复入队
_ACTIVE_STATUSES = ("pending", "uploading", "retrying", "blocked_auth")


class UploadManager:
    """单工作线程持久上传队列；渠道实现通过 provider 注册表解耦。"""

    # 上传记录只留最近这么多条（队列里未结束的任务永不删除）
    MAX_JOBS = 100

    def __init__(self, run_path: str, config_getter: Callable[[], dict], settings=None):
        self.run_path = os.path.abspath(run_path)
        self._config_getter = config_getter
        self._message_pusher = MessagePusher(settings) if settings is not None else None
        self.db_path = os.path.join(run_path, "config", "uploads.sqlite3")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._providers = {"bilibili": BilibiliProvider(run_path)}
        self._stop = threading.Event()
        self._init_db()
        self._recover_interrupted()
        self._prune_jobs()
        self._worker = threading.Thread(target=self._run, name="UploadWorker", daemon=True)
        self._worker.start()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS upload_jobs (
                    id TEXT PRIMARY KEY,
                    source_task_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    status TEXT NOT NULL,
                    files_json TEXT NOT NULL,
                    title TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    remote_id TEXT,
                    message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(source_task_id, provider)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS upload_channels (
                    provider TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'active',
                    message TEXT NOT NULL DEFAULT '',
                    notified INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._migrate_channel_columns(conn)

    @staticmethod
    def _migrate_channel_columns(conn: sqlite3.Connection) -> None:
        """老库补列：冷却到期时间与连续限流次数（只在缺失时 ALTER）。"""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(upload_channels)")}
        if "resume_at" not in columns:
            conn.execute(
                "ALTER TABLE upload_channels ADD COLUMN resume_at REAL NOT NULL DEFAULT 0"
            )
        if "cool_downs" not in columns:
            conn.execute(
                "ALTER TABLE upload_channels ADD COLUMN cool_downs INTEGER NOT NULL DEFAULT 0"
            )
        if "last_attempt_at" not in columns:
            conn.execute(
                "ALTER TABLE upload_channels ADD COLUMN last_attempt_at REAL NOT NULL DEFAULT 0"
            )

    def _recover_interrupted(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE upload_jobs SET status='retrying', next_attempt_at=0, "
                "message='service restarted during upload' WHERE status='uploading'"
            )

    def _prune_jobs(self) -> int:
        """超过 MAX_JOBS 时按创建时间删最旧的已结束记录。

        未结束（排队/上传/重试/等登录）的一律保留，否则会丢队列。
        """
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM upload_jobs").fetchone()[0]
            excess = total - self.MAX_JOBS
            if excess <= 0:
                return 0
            cursor = conn.execute(
                "DELETE FROM upload_jobs WHERE id IN ("
                "SELECT id FROM upload_jobs "
                "WHERE status NOT IN ('pending', 'uploading', 'retrying', 'blocked_auth') "
                "ORDER BY created_at ASC, rowid ASC LIMIT ?)",
                (excess,),
            )
            removed = cursor.rowcount
        if removed:
            logger.info(f"上传记录超出保留上限 {self.MAX_JOBS}，已清理 {removed} 条最旧记录")
        return removed

    @staticmethod
    def _display_title(files: list[str], source_task_id: str) -> str:
        if not files:
            return f"人体识别产物 {source_task_id}"
        stem = Path(files[0]).stem.removesuffix("_merged")
        return stem[:80] or f"人体识别产物 {source_task_id}"

    def enqueue_pose_outputs(self, source_task_id: str, state: dict) -> int:
        """为完成的自动识别任务按启用渠道建任务；数据库唯一键保证幂等。"""
        if state.get("status") != "completed" or state.get("trigger") != "auto":
            return 0
        files = [
            os.path.abspath(str(path))
            for path in (state.get("summary") or {}).get("output_files", [])
            if path and os.path.isfile(path)
        ]
        if not files:
            return 0

        uploads = self._config_getter().get("uploads") or {}
        if not uploads.get("enabled") or not uploads.get("auto_after_pose", True):
            return 0
        providers = uploads.get("providers") or {}
        now = datetime.now().isoformat(timespec="seconds")
        inserted = 0
        with self._connect() as conn:
            for name, provider_config in providers.items():
                if name not in self._providers or not isinstance(provider_config, dict):
                    continue
                if not provider_config.get("enabled"):
                    continue
                job_id = uuid.uuid4().hex
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO upload_jobs
                    (id, source_task_id, provider, status, files_json, title, metadata_json,
                     attempts, next_attempt_at, created_at, updated_at)
                    VALUES (?, ?, ?, 'pending', ?, ?, ?, 0, 0, ?, ?)
                    """,
                    (
                        job_id,
                        source_task_id,
                        name,
                        json.dumps(files, ensure_ascii=False),
                        self._display_title(files, source_task_id),
                        json.dumps({"rec_id": state.get("rec_id")}, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                inserted += cursor.rowcount
        if inserted:
            logger.info(f"人体识别产物已加入上传队列: {source_task_id} ({inserted} channel(s))")
            self._prune_jobs()
        return inserted

    def enqueue_files(self, files: list[str], providers: list[str]) -> dict:
        """手动批量上传入队（走与自动上传同一条队列）。

        去重按「文件 × 渠道」：任意来源（自动识别产物或手动选文件）的上传记录里
        已经有该文件时就不再重复上传——已成功算 ``skipped_uploaded``，还在队列里
        算 ``skipped_queued``；只有失败过的会重置成 pending 重投。
        手动任务的 ``source_task_id`` 用 ``file:<绝对路径>``，配合唯一键天然幂等。
        """
        paths = [os.path.abspath(p) for p in files if p and os.path.isfile(p)]
        result = {
            "files": len(paths),
            "queued": 0,
            "skipped_uploaded": 0,
            "skipped_queued": 0,
            "providers": [],
        }
        if not paths:
            return result

        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            for name in providers:
                if name not in self._providers or name in result["providers"]:
                    continue
                result["providers"].append(name)
                index: dict[str, list[str]] = {}
                for row in conn.execute(
                    "SELECT status, files_json FROM upload_jobs WHERE provider=?", (name,)
                ):
                    for path in json.loads(row["files_json"]):
                        index.setdefault(path, []).append(row["status"])

                for path in paths:
                    states = index.get(path) or []
                    if "completed" in states:
                        result["skipped_uploaded"] += 1
                        continue
                    if any(state in _ACTIVE_STATUSES for state in states):
                        result["skipped_queued"] += 1
                        continue

                    source_key = f"file:{path}"
                    # 失败过的同文件同渠道记录直接复用（次数清零重投），不新增行
                    reset = conn.execute(
                        "UPDATE upload_jobs SET status='pending', attempts=0, next_attempt_at=0, "
                        "message='', updated_at=? "
                        "WHERE source_task_id=? AND provider=? AND status='failed'",
                        (now, source_key, name),
                    ).rowcount
                    if not reset:
                        reset = conn.execute(
                            """
                            INSERT OR IGNORE INTO upload_jobs
                            (id, source_task_id, provider, status, files_json, title, metadata_json,
                             attempts, next_attempt_at, created_at, updated_at)
                            VALUES (?, ?, ?, 'pending', ?, ?, ?, 0, 0, ?, ?)
                            """,
                            (
                                uuid.uuid4().hex,
                                source_key,
                                name,
                                json.dumps([path], ensure_ascii=False),
                                self._file_title(path),
                                json.dumps({"origin": "manual", "source_path": path}, ensure_ascii=False),
                                now,
                                now,
                            ),
                        ).rowcount
                    if reset:
                        result["queued"] += 1
                        index.setdefault(path, []).append("pending")

        if result["queued"]:
            logger.info(
                f"手动上传已入队: {result['queued']} 个文件 · 渠道 {', '.join(result['providers'])} "
                f"（跳过已上传 {result['skipped_uploaded']}、队列中 {result['skipped_queued']}）"
            )
            self._prune_jobs()
        return result

    @staticmethod
    def _file_title(path: str) -> str:
        stem = Path(path).stem.removesuffix("_merged")
        return stem[:80] or Path(path).name[:80]

    def list_channels(self) -> list[dict]:
        configured = (self._config_getter().get("uploads") or {}).get("providers") or {}
        with self._connect() as conn:
            rows = {row["provider"]: dict(row) for row in conn.execute("SELECT * FROM upload_channels")}
        result = []
        for name, provider_config in configured.items():
            if name not in self._providers or not isinstance(provider_config, dict):
                continue
            item = rows.get(name) or {
                "provider": name,
                "status": "active",
                "message": "",
                "notified": 0,
                "updated_at": None,
                "resume_at": 0,
                "cool_downs": 0,
                "last_attempt_at": 0,
            }
            item["enabled"] = bool(provider_config.get("enabled"))
            interval = self._channel_interval(name)
            item["min_interval_seconds"] = interval
            # 下一次能投递的时刻（0 = 现在就可以）：冷却中看 resume_at，否则看节奏门。
            # 前端靠它告诉用户「点了恢复为什么队列还没动」。
            next_slot = 0.0
            if item.get("status") == "cooling":
                next_slot = float(item.get("resume_at") or 0)
            gate = float(item.get("last_attempt_at") or 0) + interval
            if gate > time.time() and gate > next_slot:
                next_slot = gate
            item["next_slot_at"] = next_slot
            result.append(item)
        return result

    def resume_channel(self, provider: str) -> bool:
        if provider not in self._providers:
            return False
        now = datetime.now().isoformat(timespec="seconds")
        now_ts = time.time()
        with self._connect() as conn:
            current = conn.execute(
                "SELECT status FROM upload_channels WHERE provider=?", (provider,)
            ).fetchone()
            # 从限流冷却里恢复：保留 cool_downs/notified——用户没先去过验证就又撞上风控时，
            # 退避继续翻倍且不重复推送（否则每次点「恢复上传」都会再拉一次 600s + 一条通知）。
            # 从登录失效里恢复不一定是新 bug，但也可能是新的 cookie：通知重新计一次。
            keep_episode = bool(current) and current["status"] == "cooling"
            notified = "notified" if keep_episode else "0"
            cool_downs = "cool_downs" if keep_episode else "0"
            conn.execute(
                "INSERT INTO upload_channels"
                "(provider,status,message,notified,updated_at,resume_at,cool_downs) "
                "VALUES(?, 'active', '', 0, ?, 0, 0) "
                "ON CONFLICT(provider) DO UPDATE SET status='active', message='', "
                f"notified={notified}, updated_at=excluded.updated_at, resume_at=0, "
                f"cool_downs={cool_downs}, last_attempt_at=0",
                (provider, now),
            )
            # 用户显式恢复：把等登录/等冷却的任务立刻放行（其他正常指数退避的不动）；
            # last_attempt_at 归零 = 也跳过节奏门，用户点了就立刻投一单
            conn.execute(
                "UPDATE upload_jobs SET status='retrying', next_attempt_at=0, updated_at=? "
                "WHERE provider=? AND (status='blocked_auth' "
                "OR (status='retrying' AND next_attempt_at > ?))",
                (now, provider, now_ts),
            )
        return True

    def list_jobs(self, page: int = 1, per_page: int = 15) -> list[dict]:
        """按创建时间倒序分页；created_at 只到秒，同秒内再用 rowid 兜底保证翻页稳定。"""
        per_page = max(1, min(per_page, self.MAX_JOBS))
        offset = (max(1, page) - 1) * per_page
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM upload_jobs ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
                (per_page, offset),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["files"] = json.loads(item.pop("files_json"))
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result.append(item)
        return result

    def count_jobs(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM upload_jobs").fetchone()[0])

    def retry(self, job_id: str) -> bool:
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE upload_jobs SET status='retrying', next_attempt_at=0, message='', updated_at=? "
                "WHERE id=? AND status IN ('failed', 'retrying')",
                (now, job_id),
            )
            return bool(cursor.rowcount)

    def _channel_interval(self, provider: str) -> float:
        """该渠道两个投稿之间的最小间隔：渠道配置优先于全局，60s 下限防手误设 0 打穿风控。"""
        uploads = self._config_getter().get("uploads") or {}
        provider_config = (uploads.get("providers") or {}).get(provider) or {}
        raw = provider_config.get("min_interval_seconds")
        if raw is None:
            raw = uploads.get("min_interval_seconds")
        try:
            return max(60.0, float(raw if raw is not None else _DEFAULT_MIN_INTERVAL_SECONDS))
        except (TypeError, ValueError):
            return float(_DEFAULT_MIN_INTERVAL_SECONDS)

    def _next_job(self):
        with self._connect() as conn:
            now_ts = time.time()
            # 多取几条候选：最早那条被节奏门挡住时，不该连带把别的渠道也锁死。
            rows = conn.execute(
                "SELECT j.*, COALESCE(c.status, 'active') AS channel_status, "
                "COALESCE(c.resume_at, 0) AS channel_resume_at, "
                "COALESCE(c.last_attempt_at, 0) AS channel_last_at "
                "FROM upload_jobs j LEFT JOIN upload_channels c ON c.provider=j.provider "
                "WHERE j.status IN ('pending','retrying') AND j.next_attempt_at <= ? "
                "AND COALESCE(c.status, 'active')='active' "
                "AND COALESCE(c.resume_at, 0) <= ? ORDER BY j.created_at LIMIT 20",
                (now_ts, now_ts),
            ).fetchall()
            row = None
            for candidate in rows:
                last_at = float(candidate["channel_last_at"] or 0)
                if last_at + self._channel_interval(candidate["provider"]) > now_ts:
                    continue  # 该渠道还没到下一个投稿窗口
                row = candidate
                break
            if row is None:
                return None
            now = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                "UPDATE upload_jobs SET status='uploading', attempts=attempts+1, updated_at=? WHERE id=?",
                (now, row["id"]),
            )
            # 节奏门计时起点是「真正开始投」那一刻，投稿耗时本身也算进间隔
            conn.execute(
                "INSERT INTO upload_channels"
                "(provider,status,message,notified,updated_at,last_attempt_at) "
                "VALUES(?, 'active', '', 0, ?, ?) "
                "ON CONFLICT(provider) DO UPDATE SET last_attempt_at=excluded.last_attempt_at",
                (row["provider"], now, now_ts),
            )
            return dict(row)

    def _run(self) -> None:
        while not self._stop.wait(2):
            row = self._next_job()
            if row is None:
                continue
            self._process(row)

    def _process(self, row: dict) -> None:
        provider_name = row["provider"]
        provider = self._providers.get(provider_name)
        uploads = self._config_getter().get("uploads") or {}
        config = (uploads.get("providers") or {}).get(provider_name) or {}
        attempts = int(row["attempts"]) + 1
        max_attempts = max(1, int(uploads.get("max_attempts") or 5))
        request = UploadRequest(
            job_id=row["id"],
            files=json.loads(row["files_json"]),
            title=row["title"],
            metadata=json.loads(row["metadata_json"]),
        )
        try:
            if provider is None:
                raise RuntimeError(f"Unknown upload provider: {provider_name}")
            result = provider.upload(request, config)
        except UploadRateLimited as exc:
            self._cool_down(row, exc, attempts)
            return
        except UploadAuthError as exc:
            self._block_auth(row, str(exc))
            return
        except Exception as exc:
            terminal = attempts >= max_attempts
            delay = _RETRY_DELAYS[min(attempts - 1, len(_RETRY_DELAYS) - 1)]
            status = "failed" if terminal else "retrying"
            with self._connect() as conn:
                conn.execute(
                    "UPDATE upload_jobs SET status=?, message=?, next_attempt_at=?, updated_at=? WHERE id=?",
                    (
                        status,
                        str(exc)[-4000:],
                        0 if terminal else time.time() + delay,
                        datetime.now().isoformat(timespec="seconds"),
                        row["id"],
                    ),
                )
            logger.error(f"上传任务失败({provider_name}, attempt={attempts}/{max_attempts}): {exc}")
            if terminal:
                self._prune_jobs()
                self._notify("StreamCap 上传失败", f"{request.title}\n渠道：{provider_name}\n{str(exc)[-500:]}")
            return

        with self._connect() as conn:
            conn.execute(
                "UPDATE upload_jobs SET status='completed', remote_id=?, message=?, "
                "next_attempt_at=0, updated_at=? WHERE id=?",
                (
                    result.remote_id,
                    result.message[-4000:],
                    datetime.now().isoformat(timespec="seconds"),
                    row["id"],
                ),
            )
            # 一单成功说明风控已解除，冷却计数归零（下次再限流从 base 重新开始）
            conn.execute(
                "UPDATE upload_channels SET cool_downs=0, resume_at=0 WHERE provider=?",
                (provider_name,),
            )
        logger.success(f"上传任务完成: {provider_name} {result.remote_id or row['id']}")
        self._prune_jobs()

    def _cool_down(self, row: dict, exc: Exception, attempts: int) -> None:
        """平台风控/限流：整个渠道冷却，任务退回重试预算。

        406/601 「上传视频过快」是账号级状态，逐任务重试只会把 5 次预算全部烧在
        限流窗口里（2026-10-28 生产：40min 内 49 条任务全烧到第 4 次）。所以：
        渠道 status=cooling + resume_at，所有任务一起停，attempts 回退 1。
        """
        uploads = self._config_getter().get("uploads") or {}
        base = max(60, int(uploads.get("rate_limit_cooldown_seconds") or _DEFAULT_COOLDOWN_SECONDS))
        cap = max(
            base, int(uploads.get("rate_limit_max_cooldown_seconds") or _DEFAULT_MAX_COOLDOWN_SECONDS)
        )
        message = str(exc)[-4000:]
        now_ts = time.time()
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            channel = conn.execute(
                "SELECT notified, cool_downs FROM upload_channels WHERE provider=?",
                (row["provider"],),
            ).fetchone()
            count = int(channel["cool_downs"] if channel else 0) + 1
            should_notify = channel is None or not bool(channel["notified"])
            delay = min(base * (2 ** (count - 1)), cap)
            resume_at = now_ts + delay
            conn.execute(
                "INSERT INTO upload_channels"
                "(provider,status,message,notified,updated_at,resume_at,cool_downs) "
                "VALUES(?, 'cooling', ?, 1, ?, ?, ?) "
                "ON CONFLICT(provider) DO UPDATE SET status='cooling', message=excluded.message, "
                "notified=1, updated_at=excluded.updated_at, resume_at=excluded.resume_at, "
                "cool_downs=excluded.cool_downs",
                (row["provider"], message, now, resume_at, count),
            )
            conn.execute(
                "UPDATE upload_jobs SET status='retrying', attempts=?, next_attempt_at=?, "
                "message=?, updated_at=? WHERE id=?",
                (max(0, attempts - 1), resume_at, message, now, row["id"]),
            )
        logger.warning(
            f"上传渠道被平台限流，冷却 {delay}s: {row['provider']} "
            f"(第 {count} 次连续限流，attempt 不计数) -> {message[-300:]}"
        )
        if should_notify:
            needs_verify = bool(getattr(exc, "needs_verify", False))
            hint = (
                "平台已经挂出待过的验证：去 B 站网页端/App 手动投稿一次，把弹出的人机或短信验证过掉，"
                if needs_verify
                else "去 B 站网页端或 App 手动投稿一次（会要求人机/短信验证），把验证过掉，"
            )
            self._notify(
                "StreamCap B站上传被限流",
                f"{hint}"
                "然后回到「设置 → 自动上传」点「恢复上传」。"
                f"积压任务不会消耗重试次数，{max(1, round(delay / 60))} 分钟后也会自动再试。",
            )

    def _block_auth(self, row: dict, message: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        should_notify = False
        with self._connect() as conn:
            channel = conn.execute(
                "SELECT notified FROM upload_channels WHERE provider=?", (row["provider"],)
            ).fetchone()
            should_notify = channel is None or not bool(channel["notified"])
            conn.execute(
                "INSERT INTO upload_channels"
                "(provider,status,message,notified,updated_at,resume_at,cool_downs) "
                "VALUES(?, 'blocked_auth', ?, 1, ?, 0, 0) "
                "ON CONFLICT(provider) DO UPDATE SET status='blocked_auth', message=excluded.message, "
                "notified=1, updated_at=excluded.updated_at, resume_at=0, cool_downs=0",
                (row["provider"], message[-4000:], now),
            )
            conn.execute(
                "UPDATE upload_jobs SET status='blocked_auth', message=?, next_attempt_at=0, updated_at=? WHERE id=?",
                (message[-4000:], now, row["id"]),
            )
        logger.error(f"上传渠道登录失效，已暂停: {row['provider']}: {message}")
        if should_notify:
            self._notify(
                "StreamCap B站登录失效",
                "Bilibili 自动上传已暂停，请重新扫码登录后在自动上传页恢复渠道。",
            )

    def _notify(self, title: str, content: str) -> None:
        if self._message_pusher is None:
            return
        config = self._config_getter().get("uploads") or {}
        if not config.get("notify_failures", True):
            return
        try:
            self._message_pusher.push_messages_sync(title, content)
        except Exception as exc:
            logger.warning(f"上传状态通知失败: {exc}")

    def shutdown(self) -> None:
        self._stop.set()
        self._worker.join(timeout=3)
