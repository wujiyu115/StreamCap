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
from .models import UploadAuthError, UploadRequest
from .providers import BilibiliProvider

_RETRY_DELAYS = (60, 300, 900, 3600)


class UploadManager:
    """单工作线程持久上传队列；渠道实现通过 provider 注册表解耦。"""

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

    def _recover_interrupted(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE upload_jobs SET status='retrying', next_attempt_at=0, "
                "message='service restarted during upload' WHERE status='uploading'"
            )

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
        return inserted

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
            }
            item["enabled"] = bool(provider_config.get("enabled"))
            result.append(item)
        return result

    def resume_channel(self, provider: str) -> bool:
        if provider not in self._providers:
            return False
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO upload_channels(provider,status,message,notified,updated_at) "
                "VALUES(?, 'active', '', 0, ?) "
                "ON CONFLICT(provider) DO UPDATE SET status='active', message='', notified=0, "
                "updated_at=excluded.updated_at",
                (provider, now),
            )
            conn.execute(
                "UPDATE upload_jobs SET status='retrying', next_attempt_at=0, updated_at=? "
                "WHERE provider=? AND status='blocked_auth'",
                (now, provider),
            )
        return True

    def list_jobs(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM upload_jobs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["files"] = json.loads(item.pop("files_json"))
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result.append(item)
        return result

    def retry(self, job_id: str) -> bool:
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE upload_jobs SET status='retrying', next_attempt_at=0, message='', updated_at=? "
                "WHERE id=? AND status IN ('failed', 'retrying')",
                (now, job_id),
            )
            return bool(cursor.rowcount)

    def _next_job(self):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT j.* FROM upload_jobs j LEFT JOIN upload_channels c ON c.provider=j.provider "
                "WHERE j.status IN ('pending','retrying') AND j.next_attempt_at <= ? "
                "AND COALESCE(c.status, 'active')='active' ORDER BY j.created_at LIMIT 1",
                (time.time(),),
            ).fetchone()
            if row is None:
                return None
            now = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                "UPDATE upload_jobs SET status='uploading', attempts=attempts+1, updated_at=? WHERE id=?",
                (now, row["id"]),
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
        logger.success(f"上传任务完成: {provider_name} {result.remote_id or row['id']}")

    def _block_auth(self, row: dict, message: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        should_notify = False
        with self._connect() as conn:
            channel = conn.execute(
                "SELECT notified FROM upload_channels WHERE provider=?", (row["provider"],)
            ).fetchone()
            should_notify = channel is None or not bool(channel["notified"])
            conn.execute(
                "INSERT INTO upload_channels(provider,status,message,notified,updated_at) "
                "VALUES(?, 'blocked_auth', ?, 1, ?) "
                "ON CONFLICT(provider) DO UPDATE SET status='blocked_auth', message=excluded.message, "
                "notified=1, updated_at=excluded.updated_at",
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
