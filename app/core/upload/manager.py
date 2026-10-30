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
from .models import (
    UploadAuthError,
    UploadLineUnavailable,
    UploadRateLimited,
    UploadRequest,
    UploadResultUnknown,
)
from .providers import BilibiliProvider, DouyinProvider

_RETRY_DELAYS = (60, 300, 900, 3600)

# 风控冷却默认值：B 站「上传视频过快」实测不手动过验证能撑 3~4 天，6h 起步 + 翻倍封顶 24h
_DEFAULT_COOLDOWN_SECONDS = 21600
_DEFAULT_MAX_COOLDOWN_SECONDS = 86400

# 投递节奏门：**每个文件（分P）** 之间的最小间隔（秒），不是每单。
# 依据：B 站 406「您上传视频过快」是从 pre_upload 抛出来的，而 biliup 对一单里的每个
# 文件各发一次 pre_upload（生产 48 条任务的输出逐条数：5P→5 次，1P→1 次，投稿成功恒 1 次）。
# 所以计价单位是文件而不是稿件：一单几 P 就占几个额度，否则「合并成一稿」会变成
# 20 秒内打 20 次 pre_upload 的突发，正是 2026-10-28 事故的形态
# （当天 13 次 pre_upload / 13 分钟 → 406；而单稿 5 次连发被反复验证安全）。
# 默认 30 分钟/文件 = 每小时 2 个 pre_upload，比出事密度低一个量级。
# 想更激进/更保守改 uploads.min_interval_seconds（或 providers.<name>.min_interval_seconds）。
_DEFAULT_MIN_INTERVAL_SECONDS = 1800

# 一单里最多放几个分P。生产实测 5 个连发安全；再多就是在赌 pre_upload 的突发阈值。
_DEFAULT_MAX_PAGES_PER_JOB = 5

# 上传线路节点证书失效后的重试间隔：已经换线了，不必拖 5 分钟
_LINE_RETRY_SECONDS = 60

# 文件整体找不到（目录也消失 = NAS 没挂上）时的重查间隔；不烧重试次数
_MISSING_RETRY_SECONDS = 900

# 「任务还没结束」的状态：同一文件再次提交时不能重复入队
# 入队去重时视为「还占着这个文件」的状态；paused 是用户手动暂停，同样不能再排一条新任务
_ACTIVE_STATUSES = ("pending", "uploading", "retrying", "blocked_auth", "paused")


class UploadManager:
    """单工作线程持久上传队列；渠道实现通过 provider 注册表解耦。"""

    # 上传记录只留最近这么多条（队列里未结束的任务永不删除）
    MAX_JOBS = 100

    def __init__(
        self,
        run_path: str,
        config_getter: Callable[[], dict],
        settings=None,
        cookie_getter: Callable[[], dict] | None = None,
    ):
        self.run_path = os.path.abspath(run_path)
        self._config_getter = config_getter
        self._message_pusher = MessagePusher(settings) if settings is not None else None
        self.db_path = os.path.join(run_path, "config", "uploads.sqlite3")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        # 抖音复用「设置 → Cookie」里那份登录态（与关注列表同一个键），不单独存
        if cookie_getter is None:
            cookie_getter = (
                (lambda: settings.get_cookies_value("douyin") or "")
                if settings is not None
                else (lambda: "")
            )
        self._cookie_getter = cookie_getter
        self._providers = {
            "bilibili": BilibiliProvider(run_path),
            "douyin": DouyinProvider(run_path, cookie_getter=cookie_getter),
        }
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
        # 节奏门到期时刻：按「这一单要占几个文件额度」预先算好，避免把 last_attempt_at
        # 写成未来时间去表达同一件事（那样排查时会看不懂）。
        if "pacing_until" not in columns:
            conn.execute(
                "ALTER TABLE upload_channels ADD COLUMN pacing_until REAL NOT NULL DEFAULT 0"
            )
        # 上一次投了几个文件：额度要按「当前」间隔重算，否则用户把间隔调小后，
        # 预扣的 pacing_until 还是按旧间隔算的，界面会一直显示旧时刻（2026-10-28 实测到）。
        if "last_attempt_files" not in columns:
            conn.execute(
                "ALTER TABLE upload_channels ADD COLUMN last_attempt_files INTEGER NOT NULL DEFAULT 0"
            )
            # 老数据不知道当时几个文件，按最小的一单（1 个）补；宁可早一点放行，也别永久卡住
            conn.execute(
                "UPDATE upload_channels SET last_attempt_files=1 WHERE last_attempt_at > 0"
            )

    def _recover_interrupted(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE upload_jobs SET status='retrying', next_attempt_at=0, "
                "message='service restarted during upload' WHERE status='uploading'"
            )

    def _prune_jobs(self) -> int:
        """超过 MAX_JOBS 时按创建时间删最旧的已结束记录。

        未结束（排队/上传/重试/等登录/手动暂停）的一律保留，否则会丢队列。
        """
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM upload_jobs").fetchone()[0]
            excess = total - self.MAX_JOBS
            if excess <= 0:
                return 0
            cursor = conn.execute(
                "DELETE FROM upload_jobs WHERE id IN ("
                "SELECT id FROM upload_jobs "
                "WHERE status NOT IN ('pending', 'uploading', 'retrying', 'blocked_auth', 'paused') "
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
        sizes: dict[str, int] = {}
        with self._connect() as conn:
            for name, provider_config in providers.items():
                if name not in self._providers or not isinstance(provider_config, dict):
                    continue
                if not provider_config.get("enabled"):
                    continue
                # 每个渠道各算各的分片大小：B 站一稿可放多个分P，抖音一作品只能一个视频
                size = self._max_pages(name)
                sizes[name] = size
                # 一单最多 size 个分P：生产实测 5 个连发安全，再多就是在赌 pre_upload 的突发阈值
                groups = [files[i : i + size] for i in range(0, len(files), size)]
                for index, group in enumerate(groups):
                    # 只有拆成多稿时才加后缀，单片仍用原 key：老库里已存在的任务不会被重复入队
                    source_key = source_task_id if len(groups) == 1 else f"{source_task_id}#{index + 1}"
                    title = self._display_title(group, source_task_id)
                    if len(groups) > 1:
                        title = f"{title} ({index + 1}/{len(groups)})"[:80]
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
                            source_key,
                            name,
                            json.dumps(group, ensure_ascii=False),
                            title,
                            json.dumps({"rec_id": state.get("rec_id")}, ensure_ascii=False),
                            now,
                            now,
                        ),
                    )
                    inserted += cursor.rowcount
        if inserted:
            detail = ", ".join(f"{name} x 最多 {size} 分P" for name, size in sizes.items())
            logger.info(f"人体识别产物已加入上传队列: {source_task_id} ({inserted} job(s) · {detail})")
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
            counts = {
                row["provider"]: (row["total"], row["active"])
                for row in conn.execute(
                    "SELECT provider, COUNT(*) AS total, "
                    "SUM(CASE WHEN status IN ('pending','retrying') THEN 1 ELSE 0 END) AS active "
                    "FROM upload_jobs GROUP BY provider"
                )
            }
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
                "pacing_until": 0,
                "last_attempt_files": 0,
            }
            item["enabled"] = bool(provider_config.get("enabled"))
            interval = self._channel_interval(name)
            item["min_interval_seconds"] = interval
            # 该渠道自己的间隔是不是被单独覆盖过（设置页要能区分「跟全局」和「自己设的」）
            item["interval_overridden"] = provider_config.get("min_interval_seconds") is not None
            item["supports_multi_parts"] = self._max_pages(name) > 1
            total_jobs, active_jobs = counts.get(name, (0, 0))
            item["total_jobs"] = int(total_jobs or 0)
            item["active_jobs"] = int(active_jobs or 0)
            # 下一次能投递的时刻（0 = 现在就可以）：冷却中看 resume_at，手动暂停看「恢复后再说」，
            # 否则看节奏门。前端靠它告诉用户「点了恢复为什么队列还没动」。
            next_slot = 0.0
            if item.get("status") == "cooling":
                next_slot = float(item.get("resume_at") or 0)
            gate = self._gate_at(item)
            if gate > time.time() and gate > next_slot:
                next_slot = gate
            item["next_slot_at"] = next_slot
            result.append(item)
        return result

    def pause_channel(self, provider: str) -> bool:
        """手动暂停整条渠道：不再领取新任务，正在跑的那单跑完为止。

        与冷却（cooling）的区别：冷却由平台限流自动触发且带到期时间，这个纯手动、不会自己恢复。
        不动 last_attempt_at / cool_downs——恢复后仍要遵守节奏门，免得一点「继续」就又撞 406。
        """
        if provider not in self._providers:
            return False
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO upload_channels"
                "(provider,status,message,notified,updated_at,resume_at,cool_downs) "
                "VALUES(?, 'paused', '', 0, ?, 0, 0) "
                "ON CONFLICT(provider) DO UPDATE SET status='paused', message='', "
                "updated_at=excluded.updated_at, resume_at=0",
                (provider, now),
            )
        return True

    def resume_channel(self, provider: str) -> bool:
        if provider not in self._providers:
            return False
        now = datetime.now().isoformat(timespec="seconds")
        now_ts = time.time()
        with self._connect() as conn:
            current = conn.execute(
                "SELECT status FROM upload_channels WHERE provider=?", (provider,)
            ).fetchone()
            current_status = (current["status"] if current else "active")
            # 从限流冷却里恢复：保留 cool_downs/notified——用户没先去过验证就又撞上风控时，
            # 退避继续翻倍且不重复推送（否则每次点「恢复上传」都会再拉一次 600s + 一条通知）。
            # 从登录失效里恢复不一定是新 bug，但也可能是新的 cookie：通知重新计一次。
            keep_episode = current_status in ("cooling", "paused")
            notified = "notified" if keep_episode else "0"
            cool_downs = "cool_downs" if keep_episode else "0"
            # 手动暂停的渠道恢复时不清节奏门：暂停前刚投过一单的话，立刻再投就是自找 406。
            gate_reset = "" if current_status == "paused" else (
                ", last_attempt_at=0, pacing_until=0, last_attempt_files=0"
            )
            conn.execute(
                "INSERT INTO upload_channels"
                "(provider,status,message,notified,updated_at,resume_at,cool_downs) "
                "VALUES(?, 'active', '', 0, ?, 0, 0) "
                "ON CONFLICT(provider) DO UPDATE SET status='active', message='', "
                f"notified={notified}, updated_at=excluded.updated_at, resume_at=0, "
                f"cool_downs={cool_downs}{gate_reset}",
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

    def list_jobs(self, page: int = 1, per_page: int = 15, provider: str | None = None) -> list[dict]:
        """按创建时间倒序分页；created_at 只到秒，同秒内再用 rowid 兜底保证翻页稳定。

        ``provider`` 非空时只返回该渠道的任务（设置页按渠道分 tab，各看各的）。
        """
        per_page = max(1, min(per_page, self.MAX_JOBS))
        offset = (max(1, page) - 1) * per_page
        where = " WHERE provider=?" if provider else ""
        params: tuple = (provider, per_page, offset) if provider else (per_page, offset)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM upload_jobs{where} ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["files"] = json.loads(item.pop("files_json"))
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result.append(item)
        return result

    def count_jobs(self, provider: str | None = None) -> int:
        with self._connect() as conn:
            if provider:
                return int(
                    conn.execute("SELECT COUNT(*) FROM upload_jobs WHERE provider=?", (provider,)).fetchone()[0]
                )
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

    def pause(self, job_id: str) -> bool:
        """手动暂停一条还没开投的任务。

        uploading 的不能暂停：biliup 子进程已经把字节发出去了，暂停只能是杀进程，
        半截任务会变成「不知道投没投成功」，比等它跑完更糟，所以这里直接不认。
        """
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE upload_jobs SET status='paused', next_attempt_at=0, updated_at=? "
                "WHERE id=? AND status IN ('pending', 'retrying')",
                (now, job_id),
            )
            return bool(cursor.rowcount)

    def resume_job(self, job_id: str) -> bool:
        """恢复手动暂停的任务；顺手清掉退避时间，用户点了就立刻排队。"""
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE upload_jobs SET status='pending', next_attempt_at=0, updated_at=? "
                "WHERE id=? AND status='paused'",
                (now, job_id),
            )
            return bool(cursor.rowcount)

    def delete(self, job_id: str) -> bool:
        """删除一条上传记录/任务。

        正在上传的不让删：worker 手里还握着这一行，跑完还会回来写 remote_id，
        先删掉会得到一条「幽灵任务」。要放弃就等它跑完再删。
        """
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM upload_jobs WHERE id=? AND status!='uploading'",
                (job_id,),
            )
            return bool(cursor.rowcount)

    def is_uploading(self, job_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT status FROM upload_jobs WHERE id=?", (job_id,)).fetchone()
        return bool(row and row["status"] == "uploading")

    def _channel_interval(self, provider: str) -> float:
        """每个文件（分P）之间的最小间隔：渠道配置优先于全局，60s 下限防手误设 0 打穿风控。"""
        uploads = self._config_getter().get("uploads") or {}
        provider_config = (uploads.get("providers") or {}).get(provider) or {}
        raw = provider_config.get("min_interval_seconds")
        if raw is None:
            raw = uploads.get("min_interval_seconds")
        try:
            return max(60.0, float(raw if raw is not None else _DEFAULT_MIN_INTERVAL_SECONDS))
        except (TypeError, ValueError):
            return float(_DEFAULT_MIN_INTERVAL_SECONDS)

    def max_pages_per_job(self) -> int:
        """一单最多几个分P（识别产物切片按它切）。"""
        uploads = self._config_getter().get("uploads") or {}
        try:
            value = int(uploads.get("max_pages_per_job") or _DEFAULT_MAX_PAGES_PER_JOB)
        except (TypeError, ValueError):
            return _DEFAULT_MAX_PAGES_PER_JOB
        return max(1, value)

    def _max_pages(self, provider: str) -> int:
        """该渠道一单能放几个文件：不支持多 P 的（抖音）恒为 1。"""
        impl = self._providers.get(provider)
        if impl is not None and not getattr(impl, "supports_multi_parts", True):
            return 1
        return self.max_pages_per_job()

    def _enabled_providers(self) -> set[str]:
        """当前允许派发任务的渠道：渠道开关关掉的连队列里的旧任务也不领。

        ``uploads.enabled`` 是总闸：关掉就不再派发任何渠道（队列原样保留），
        和「关闭后什么都不投」这个直觉一致，不用去逐渠道关一遍。
        """
        uploads = self._config_getter().get("uploads") or {}
        if not uploads.get("enabled"):
            return set()
        enabled = set()
        for name, provider_config in (uploads.get("providers") or {}).items():
            if name in self._providers and isinstance(provider_config, dict) and provider_config.get("enabled"):
                enabled.add(name)
        return enabled

    def _slot_seconds(self, provider: str, file_count: int) -> float:
        """这一单要占多少额度：每个文件一次 pre_upload，所以按文件数计价。"""
        return self._channel_interval(provider) * max(1, int(file_count))

    def _gate_at(self, channel) -> float:
        """节奏门放行时刻：按**当前**间隔 × 上次投的文件数算，改间隔立即生效。

        pacing_until 是领取时按当时间隔预扣的值，只作兜底（老数据 / 文件数未知），
        否则用户把间隔调小以后还得等旧额度走完，界面上看起来像「改了没用」。
        """
        last_at = float(channel.get("last_attempt_at") or 0)
        files = int(channel.get("last_attempt_files") or 0)
        if last_at > 0:
            # files=0 是迁移前的老行（文件数未知），按最小的一单算，别永久卡住
            return last_at + self._slot_seconds(channel.get("provider") or "", max(1, files))
        return float(channel.get("pacing_until") or 0)

    def _next_job(self):
        with self._connect() as conn:
            now_ts = time.time()
            # 多取几条候选：最早那条被节奏门挡住时，不该连带把别的渠道也锁死。
            rows = conn.execute(
                "SELECT j.*, COALESCE(c.status, 'active') AS channel_status, "
                "COALESCE(c.resume_at, 0) AS channel_resume_at, "
                "COALESCE(c.last_attempt_at, 0) AS channel_last_at, "
                "COALESCE(c.pacing_until, 0) AS channel_pacing_until, "
                "COALESCE(c.last_attempt_files, 0) AS channel_last_files "
                "FROM upload_jobs j LEFT JOIN upload_channels c ON c.provider=j.provider "
                "WHERE j.status IN ('pending','retrying') AND j.next_attempt_at <= ? "
                "AND COALESCE(c.status, 'active')='active' "
                "AND COALESCE(c.resume_at, 0) <= ? ORDER BY j.created_at LIMIT 20",
                (now_ts, now_ts),
            ).fetchall()
            row = None
            enabled = self._enabled_providers()
            for candidate in rows:
                if candidate["provider"] not in enabled:
                    continue  # 渠道开关关掉（或总闸关掉）：不领新任务，队列原样留着
                if self._gate_at(
                    {
                        "provider": candidate["provider"],
                        "last_attempt_at": candidate["channel_last_at"],
                        "last_attempt_files": candidate["channel_last_files"],
                        "pacing_until": candidate["channel_pacing_until"],
                    }
                ) > now_ts:
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
            # 节奏门计时起点是「真正开始投」那一刻，投稿耗时本身也算进间隔；
            # 一单有 N 个文件就一次性占掉 N 个文件额度（pre_upload 是按文件发的）。
            file_count = len(json.loads(row["files_json"]))
            conn.execute(
                "INSERT INTO upload_channels"
                "(provider,status,message,notified,updated_at,last_attempt_at,pacing_until,"
                "last_attempt_files) "
                "VALUES(?, 'active', '', 0, ?, ?, ?, ?) "
                "ON CONFLICT(provider) DO UPDATE SET last_attempt_at=excluded.last_attempt_at, "
                "pacing_until=excluded.pacing_until, "
                "last_attempt_files=excluded.last_attempt_files",
                (
                    row["provider"],
                    now,
                    now_ts,
                    now_ts + self._slot_seconds(row["provider"], file_count),
                    file_count,
                ),
            )
            return dict(row)

    def _run(self) -> None:
        while not self._stop.wait(2):
            row = self._next_job()
            if row is None:
                continue
            self._process(row)

    def _files_on_disk(self, row: dict) -> list[str]:
        """投递前确认文件还在：磁盘上的文件被删了就跳过，别让 biliup 报错烧光重试次数。

        三种情形：
        - 部分缺失 → 缩成剩下的分P 继续投，message 里备注缺了几个
        - 全缺但目录还在 → 真被删了，直接判 failed（重试没意义）
        - 全缺且目录也没了 → 存储没挂上（NAS 掉线），放回 retrying 稍后再试，不烧额度
        """
        files = json.loads(row["files_json"])
        missing = [path for path in files if not os.path.exists(path)]
        if not missing:
            return files
        remaining = [path for path in files if os.path.exists(path)]
        now = datetime.now().isoformat(timespec="seconds")
        names = ", ".join(Path(path).name for path in missing)[:160]
        with self._connect() as conn:
            if remaining:
                conn.execute(
                    "UPDATE upload_jobs SET files_json=?, message=?, updated_at=? WHERE id=?",
                    (
                        json.dumps(remaining),
                        f"{len(missing)} of {len(files)} file(s) missing on disk, skipped",
                        now,
                        row["id"],
                    ),
                )
            elif any(os.path.isdir(os.path.dirname(path) or ".") for path in files):
                conn.execute(
                    "UPDATE upload_jobs SET status='failed', message=?, next_attempt_at=0, "
                    "attempts=MAX(0, attempts-1), updated_at=? WHERE id=?",
                    (f"file(s) deleted from disk: {names}", now, row["id"]),
                )
            else:
                conn.execute(
                    "UPDATE upload_jobs SET status='retrying', message=?, next_attempt_at=?, "
                    "attempts=MAX(0, attempts-1), updated_at=? WHERE id=?",
                    (
                        f"storage unavailable, files not found: {names}",
                        time.time() + _MISSING_RETRY_SECONDS,
                        now,
                        row["id"],
                    ),
                )
        logger.warning(
            f"上传任务 {row['id']} 有 {len(missing)}/{len(files)} 个文件不在磁盘上，"
            f"剩 {len(remaining)} 个：{names}"
        )
        return remaining

    def _fail_no_retry(self, row: dict, message: str, title: str) -> None:
        """投稿动作已发出但没有回执：一次判 failed，绝不自动重试。

        重试的后果是把同一条作品再发一遍（抖音 2026-10-30 生产就踩过：点完发布浏览器
        崩了，作品实际上已发布成功，只是没等到 toast）。核验过的渠道会在 provider 里
        先按标题核验，核到了直接算成功，走不到这里。
        """
        now = datetime.now().isoformat(timespec="seconds")
        hint = "请到平台创作者中心确认作品是否已发布，确认没发出去再手动重投"
        stored = f"{message}\n{hint}"[-4000:]
        with self._connect() as conn:
            conn.execute(
                "UPDATE upload_jobs SET status='failed', message=?, next_attempt_at=0, updated_at=? "
                "WHERE id=?",
                (stored, now, row["id"]),
            )
        logger.error(f"上传结果未知，停止自动重试({row['provider']}): {message}")
        self._notify(
            "StreamCap 上传结果未知",
            f"{title}\n渠道：{row['provider']}\n{message[-500:]}\n"
            "（投稿动作已经发出，但没拿到回执也不能从作品的列表核到，请人工确认后再决定是否重投）",
        )
        self._prune_jobs()

    def _process(self, row: dict) -> None:
        provider_name = row["provider"]
        provider = self._providers.get(provider_name)
        uploads = self._config_getter().get("uploads") or {}
        config = (uploads.get("providers") or {}).get(provider_name) or {}
        files = self._files_on_disk(row)
        if not files:
            return
        attempts = int(row["attempts"]) + 1
        max_attempts = max(1, int(uploads.get("max_attempts") or 5))
        request = UploadRequest(
            job_id=row["id"],
            files=files,
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
        except UploadLineUnavailable as exc:
            self._retry_other_line(row, exc, attempts)
            return
        except UploadAuthError as exc:
            self._block_auth(row, str(exc))
            return
        except UploadResultUnknown as exc:
            self._fail_no_retry(row, str(exc), request.title)
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
            label = self._channel_label(row["provider"])
            where = "网页端/App" if row["provider"] == "bilibili" else "创作者中心网页端/App"
            action = "手动投稿一次" if row["provider"] == "bilibili" else "手动发布一条"
            hint = (
                f"平台已经挂出待过的验证：去{label}{where}{action}，把弹出的人机或短信验证过掉，"
                if needs_verify
                else f"去{label}{where}{action}（会要求人机/短信验证），把验证过掉，"
            )
            self._notify(
                f"StreamCap {label}上传被限流",
                f"{hint}"
                "然后回到「设置 → 自动上传」点「恢复上传」。"
                f"积压任务不会消耗重试次数，{max(1, round(delay / 60))} 分钟后也会自动再试。",
            )

    def _retry_other_line(self, row: dict, exc: Exception, attempts: int) -> None:
        """上传线路的节点证书失效（平台侧坏节点）：换线重试，不烧重试预算。

        provider 已经自动钉了一条备用线路，这里只需排一次短重试。不判 failed：
        这不是内容/账号问题，熬完 5 次预算变成 failed 反而要人工重提。
        """
        now_ts = time.time()
        now = datetime.now().isoformat(timespec="seconds")
        delay = _LINE_RETRY_SECONDS
        message = str(exc)[-4000:]
        with self._connect() as conn:
            conn.execute(
                "UPDATE upload_jobs SET status='retrying', attempts=?, next_attempt_at=?, "
                "message=?, updated_at=? WHERE id=?",
                (max(0, attempts - 1), now_ts + delay, message, now, row["id"]),
            )
        logger.warning(
            f"上传线路不可用，{delay}s 后换线重试（attempt 不计数）: {row['provider']} -> 见任务日志"
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
            label = self._channel_label(row["provider"])
            if row["provider"] == "douyin":
                content = (
                    "抖音自动上传已暂停：请到「设置 → Cookie」重新扫码或粘贴抖音登录态（与关注列表共用），"
                    "保存后回到「设置 → 自动上传」恢复该渠道队列。"
                )
            else:
                content = "Bilibili 自动上传已暂停，请重新扫码登录后在自动上传页恢复渠道。"
            self._notify(f"StreamCap {label}登录失效", content)

    def _channel_label(self, provider: str) -> str:
        impl = self._providers.get(provider)
        return str(getattr(impl, "display_name", provider))

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
