import asyncio
import random
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from ...messages import message_pusher
from ...models.recording.recording_model import Recording
from ...models.recording.recording_status_model import RecordingStatus
from ...utils import utils
from ...utils.logger import logger
from ..analytics.analytics_store import AnalyticsStore
from ..platforms import room_validity
from ..platforms.platform_handlers import get_platform_info
from ..runtime.process_manager import BackgroundService
from .stream_manager import LiveStreamRecorder


class GlobalRecordingState:
    recordings = []
    lock = threading.Lock()


class RecordingManager:
    ROOM_VALIDITY_TIMEOUT_SECONDS = 20
    ROOM_VALIDITY_CACHE_TTL_SECONDS = 6 * 3600
    ROOM_VALIDITY_DEFAULT_LIMIT = 30

    def __init__(self, services):
        self.services = services
        self.settings = services.settings_config
        self.periodic_task_started = False
        self.loop_time_seconds = None
        self.load_recordings()
        self.validity_cache = services.config_manager.load_validity_cache_config() or {}
        self.analytics = AnalyticsStore(services.config_manager.analytics_dir)
        self._ = {}
        self.load()
        self.initialize_dynamic_state()
        self.max_concurrent = int(self.settings.user_config.get("platform_max_concurrent_requests", 3))
        self.preset_concurrent = self.max_concurrent
        self.platform_semaphores = defaultdict(lambda: asyncio.Semaphore(self.max_concurrent))
        self.active_recorders = {}

        # 监控自保护状态：近期取流成败滑窗（动态并发用）与轮内失败计数（全局延迟用）
        self._request_results: deque[tuple[bool, float]] = deque(maxlen=100)
        self._results_lock = threading.Lock()
        self._round_failures = 0
        self._last_concurrency_adjust = 0.0

        # 平台请求节奏门：相邻请求发起时刻的最小间隔（防风控爆发），与
        # 周期监控、有效性检测共享；锁与信号量同样只在后台 loop 使用
        self._request_spacing_locks = defaultdict(asyncio.Lock)
        self._next_slot_at: dict[str, float] = {}

    # ── 监控自保护：配置读取 ──────────────────────────────

    def _monitor_config(self) -> dict:
        cfg = self.settings.user_config
        return {
            "jitter_ratio": float(cfg.get("monitor_jitter_ratio", 0.1) or 0),
            "backoff_enabled": bool(cfg.get("monitor_failure_backoff_enabled", True)),
            "backoff_max": max(1, int(cfg.get("monitor_failure_backoff_max_multiplier", 4))),
            "global_error_delay": max(0, int(cfg.get("monitor_global_error_delay_seconds", 60))),
            "global_error_threshold": max(1, int(cfg.get("monitor_global_error_threshold", 20))),
            "post_record_recheck": max(0, int(cfg.get("monitor_post_record_recheck_seconds", 30))),
            "unsupported_limit": max(0, int(cfg.get("monitor_unsupported_failure_limit", 10))),
            "dynamic_concurrency": bool(cfg.get("monitor_dynamic_concurrency_enabled", True)),
            "auto_stop_monitor_days": max(0.0, float(cfg.get("auto_stop_monitor_days", 0) or 0)),
            "platform_min_interval": max(0.0, float(cfg.get("monitor_platform_min_interval_seconds", 2) or 0)),
            "recency_priority_enabled": bool(cfg.get("monitor_recency_priority_enabled", True)),
        }

    def _record_request_result(self, ok: bool, platform_key: str | None = None) -> None:
        with self._results_lock:
            self._request_results.append((ok, time.time()))
            if not ok:
                self._round_failures += 1
        if platform_key:
            self.analytics.record_check(platform_key, ok, time.time())
            self.analytics.maybe_flush()

    @asynccontextmanager
    async def _platform_slot(self, platform_key: str):
        """平台请求节奏门：信号量限并发，最小间隔限制请求发起速率。

        相邻两次请求的发起时刻至少隔 platform_min_interval 秒，把「一轮
        任务在数秒内爆发完」摊成匀速流，避免超出平台单位时间容忍度触发
        风控。周期监控与有效性检测共用同一份节奏预算。interval<=0 时仅
        保留并发限制（保持旧行为）。
        """
        interval = self._monitor_config()["platform_min_interval"]
        async with self.platform_semaphores[platform_key]:
            if interval > 0:
                async with self._request_spacing_locks[platform_key]:
                    wait = self._next_slot_at.get(platform_key, 0.0) - time.monotonic()
                    if wait > 0:
                        await asyncio.sleep(wait)
                    self._next_slot_at[platform_key] = time.monotonic() + interval
            yield

    def _reset_round_failures(self) -> None:
        with self._results_lock:
            self._round_failures = 0

    def _error_rate(self) -> float:
        """近 100 次取流请求的错误率（动态并发降级依据）。"""
        with self._results_lock:
            if not self._request_results:
                return 0.0
            return sum(1 for ok, _ in self._request_results if not ok) / len(self._request_results)

    def _maybe_adjust_concurrency(self) -> None:
        """错误率驱动动态并发：>50% 降 1、<25% 回升 1（5 分钟内至多调一次）。

        信号量是 defaultdict，按需重建，因此无需关心当前是否已存在。
        """
        cfg = self._monitor_config()
        if not cfg["dynamic_concurrency"]:
            return
        now = time.time()
        if now - self._last_concurrency_adjust < 300:
            return

        rate = self._error_rate()
        current = self.max_concurrent
        target = current
        if rate > 0.5 and current > 1:
            target = current - 1
        elif rate < 0.25 and current < self.preset_concurrent:
            target = current + 1
        if target != current:
            self.max_concurrent = target
            self.platform_semaphores.clear()
            self._last_concurrency_adjust = now
            logger.warning(
                f"取流错误率 {rate:.0%}，平台并发数动态调整为 {target}（原 {current}）"
            )
        else:
            self._last_concurrency_adjust = now

    @property
    def recordings(self):
        return GlobalRecordingState.recordings

    @recordings.setter
    def recordings(self, value):
        raise AttributeError("Please use add_recording/update_recording methods to modify data")

    def load(self):
        language = self.services.language_manager.language
        for key in ("recording_manager", "video_quality"):
            self._.update(language.get(key, {}))

    def load_recordings(self):
        """Load recordings from a JSON file into objects."""
        recordings_data = self.services.config_manager.load_recordings_config()
        if not GlobalRecordingState.recordings:
            GlobalRecordingState.recordings = [Recording.from_dict(rec) for rec in recordings_data]
        logger.info(f"Live Recordings: Loaded {len(self.recordings)} items")

    def initialize_dynamic_state(self):
        """Initialize dynamic state for all recordings."""
        loop_time_seconds = self.settings.user_config.get("loop_time_seconds")
        self.loop_time_seconds = int(loop_time_seconds or 300)
        for recording in self.recordings:
            recording.loop_time_seconds = self.loop_time_seconds
            recording.update_title(self._.get(recording.quality, recording.quality))
            recording.showed_checking_status = True

    async def add_recording(self, recording):
        with GlobalRecordingState.lock:
            GlobalRecordingState.recordings.append(recording)
            await self.persist_recordings()

    async def remove_recording(self, recording: Recording):
        with GlobalRecordingState.lock:
            GlobalRecordingState.recordings.remove(recording)
            await self.persist_recordings()

    async def clear_all_recordings(self):
        with GlobalRecordingState.lock:
            GlobalRecordingState.recordings.clear()
            await self.persist_recordings()

    async def persist_recordings(self):
        """Persist recordings to a JSON file."""
        data_to_save = [rec.to_dict() for rec in self.recordings]
        await self.services.config_manager.save_recordings_config(data_to_save)

    async def update_recording_card(self, recording: Recording, updated_info: dict):
        """Update an existing recording object and persist changes to a JSON file."""
        if recording:
            recording.update(updated_info)
            self.services.run_coro(self.persist_recordings())

    @staticmethod
    async def _update_recording(
        recording: Recording, monitor_status: bool, display_title: str, status_info: str, selected: bool
    ):
        attrs_update = {
            "monitor_status": monitor_status,
            "display_title": display_title,
            "status_info": status_info,
            "selected": selected,
        }
        for attr, value in attrs_update.items():
            setattr(recording, attr, value)

    async def start_monitor_recording(self, recording: Recording, auto_save: bool = True):
        """
        Start monitoring a single recording if it is not already being monitored.
        """
        if not recording.monitor_status:
            recording.is_checking = True
            recording.is_live = False
            recording.showed_checking_status = False
            # 重新开启监控视为人工干预：宽限期从此重新起算，避免自动停
            # 之后一开启就因旧时间戳被再次停掉
            recording.last_live_time = time.time()
            await self._update_recording(
                recording=recording,
                monitor_status=True,
                display_title=recording.title,
                status_info=RecordingStatus.STATUS_CHECKING,
                selected=False,
            )

            self.services.broadcast_card_update(recording)
            self.services.broadcast_pubsub("update", recording)

            self.services.run_coro(self.check_if_live(recording))

            if auto_save:
                self.services.run_coro(self.persist_recordings())

    async def stop_monitor_recording(self, recording: Recording, auto_save: bool = True):
        """
        Stop monitoring a single recording if it is currently being monitored.
        """
        if recording.monitor_status:
            await self._update_recording(
                recording=recording,
                monitor_status=False,
                display_title=f"[{self._['monitor_stopped']}] {recording.title}",
                status_info=RecordingStatus.STOPPED_MONITORING,
                selected=False,
            )
            self.stop_recording(recording, manually_stopped=True)
            self.services.broadcast_card_update(recording)
            self.services.broadcast_pubsub("update", recording)
            if auto_save:
                self.services.run_coro(self.persist_recordings())

    async def start_monitor_recordings(self):
        """
        Start monitoring multiple recordings based on user selection or all recordings if none are selected.
        """
        selected_recordings = await self.get_selected_recordings()
        pre_start_monitor_recordings = selected_recordings or self.recordings
        for recording in pre_start_monitor_recordings:
            self.services.run_coro(self.start_monitor_recording(recording, auto_save=False))
        self.services.run_coro(self.persist_recordings())
        logger.info(f"Batch Start Monitor Recordings: {[i.rec_id for i in pre_start_monitor_recordings]}")

    async def stop_monitor_recordings(self, selected_recordings: list[Recording | None] | None = None):
        """
        Stop monitoring multiple recordings based on user selection or all recordings if none are selected.
        """
        if not selected_recordings:
            selected_recordings = await self.get_selected_recordings()
        pre_stop_monitor_recordings = selected_recordings or self.recordings
        for recording in pre_stop_monitor_recordings:
            if recording is None:
                continue
            self.services.run_coro(self.stop_monitor_recording(recording, auto_save=False))
        self.services.run_coro(self.persist_recordings())
        logger.info(
            f"Batch Stop Monitor Recordings: {[i.rec_id for i in pre_stop_monitor_recordings if i is not None]}"
        )

    async def get_selected_recordings(self):
        return [recording for recording in self.recordings if recording.selected]

    async def remove_recordings(self, recordings: list[Recording]):
        """Remove a recording from the list and update the JSON file."""
        for recording in recordings:
            if recording in self.recordings:
                await self.remove_recording(recording)
                logger.info(f"Delete Items: {recording.rec_id}-{recording.streamer_name}")

    def find_recording_by_id(self, rec_id: str):
        """Find a recording by its ID (hash of dict representation)."""
        for rec in self.recordings:
            if rec.rec_id == rec_id:
                return rec
        return None

    async def check_room_validity(
        self,
        rec_ids: list[str] | None = None,
        force: bool = False,
        force_since: float = 0,
        limit: int | None = None,
    ) -> dict:
        """检测录制配置的直播间是否仍存在（只读，不改任务监控状态）。

        结果缓存在 config/room_validity.json：已判定失效的任务与 TTL 内
        的有效任务直接命中缓存不再发请求；error 不缓存（下次必重检）。
        limit 限制本次实际检测的条数（0 = 只取快照不发请求），返回
        pending 表示剩余待检数，配合前端分批拉取；force+force_since
        为全量重检：checked_at 不早于 force_since 的条目视为已检。
        """
        if limit is None:
            limit = self.ROOM_VALIDITY_DEFAULT_LIMIT
        limit = max(0, int(limit))
        if force and not force_since:
            force_since = time.time()

        targets = self.recordings if not rec_ids else []
        not_found: list[str] = []
        if rec_ids:
            id_set = set(rec_ids)
            for rec in self.recordings:
                if rec.rec_id in id_set:
                    targets.append(rec)
            not_found = list(id_set - {rec.rec_id for rec in targets})

        cache = self.validity_cache
        now = time.time()

        # 清理已删除任务的缓存条目
        valid_ids = {rec.rec_id for rec in self.recordings}
        stale_keys = [k for k in cache if k not in valid_ids]
        for k in stale_keys:
            cache.pop(k, None)

        results, misses = [], []
        for rec in targets:
            entry = cache.get(rec.rec_id)
            if self._validity_cache_hit(entry, rec.url, force, force_since, now):
                results.append(self._validity_cached_result(rec, entry))
            else:
                misses.append(rec)

        to_check = misses[:limit]
        pending = len(misses) - len(to_check)
        if to_check:
            checked = await asyncio.gather(*(self._check_single_room_validity(rec, cache) for rec in to_check))
            results.extend(checked)

        if stale_keys or to_check:
            await self.services.config_manager.save_validity_cache_config(cache)

        return {"results": results, "not_found": not_found, "pending": pending}

    def _validity_cache_hit(self, entry, url, force: bool, force_since: float, now: float) -> bool:
        if not entry or entry.get("url") != url:
            return False
        if force:
            return entry.get("checked_at", 0) >= force_since
        status = entry.get("status")
        if status == room_validity.STATUS_INVALID:
            return True
        if status in (room_validity.STATUS_LIVE, room_validity.STATUS_OFFLINE):
            return now - entry.get("checked_at", 0) < self.ROOM_VALIDITY_CACHE_TTL_SECONDS
        return False

    @staticmethod
    def _validity_cached_result(recording: Recording, entry: dict) -> dict:
        return {
            "rec_id": recording.rec_id,
            "streamer_name": recording.streamer_name,
            "url": recording.url,
            "platform": recording.platform,
            "platform_key": recording.platform_key,
            "status": entry.get("status"),
            "anchor_name": entry.get("anchor_name"),
            "title": entry.get("title"),
            "detail": entry.get("detail"),
            "precise": entry.get("precise", False),
            "checked_at": entry.get("checked_at"),
            "cached": True,
        }

    async def _check_single_room_validity(self, recording: Recording, cache: dict) -> dict:
        url = (recording.url or "").strip()
        platform, platform_key = get_platform_info(url)
        platform = platform or recording.platform
        platform_key = platform_key or recording.platform_key
        cookies = self.settings.cookies_config.get(platform_key)
        proxy = room_validity.resolve_platform_proxy(self.settings.user_config, platform_key)
        account = self.settings.accounts_config.get(platform_key, {}) if platform_key else {}

        async with self._platform_slot(platform_key):
            # 请求间隔抖动：大批量检测时拉开请求节奏，降低平台风控概率
            await asyncio.sleep(random.uniform(0.3, 0.8))
            try:
                result = await asyncio.wait_for(
                    room_validity.check_room_validity(
                        url,
                        platform=platform,
                        platform_key=platform_key,
                        proxy=proxy,
                        cookies=cookies,
                        record_quality=recording.quality,
                        account=account,
                    ),
                    timeout=self.ROOM_VALIDITY_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                result = room_validity.RoomValidityResult(
                    status=room_validity.STATUS_ERROR, detail=f"检测超时（>{self.ROOM_VALIDITY_TIMEOUT_SECONDS}s）"
                )
            except Exception as e:
                logger.error(f"Room validity check failed: {recording.url} - {type(e).__name__}: {e}")
                result = room_validity.RoomValidityResult(status=room_validity.STATUS_ERROR, detail=f"{type(e).__name__}: {e}")

        now = time.time()
        if result.status != room_validity.STATUS_ERROR:
            cache[recording.rec_id] = {
                "url": recording.url,
                "status": result.status,
                "anchor_name": result.anchor_name,
                "title": result.title,
                "detail": result.detail,
                "precise": result.precise,
                "checked_at": now,
            }

        return {
            "rec_id": recording.rec_id,
            "streamer_name": recording.streamer_name,
            "url": recording.url,
            "platform": platform or recording.platform,
            "platform_key": platform_key or recording.platform_key,
            **result.to_dict(),
            "checked_at": now,
            "cached": False,
        }

    async def check_all_live_status(self):
        """每轮调度：按抖动/退避/快检/新近度决定哪些任务该查，跳过 unsupported。

        - 抖动：每个任务的下次可检时间加 ±jitter_ratio 随机偏移，打散集中请求
        - 退避：连续失败任务的间隔乘 backoff_multiplier（翻倍至上限，成功重置）
        - 快检：刚录制结束的任务 post_record_recheck 秒后即可重检（防卡顿少录）
        - 新近度：最近开播的房间每轮都查，长期未开播的拉长间隔让出请求预算
        - unsupported：连续失败超限的任务本轮跳过
        """
        cfg = self._monitor_config()
        now = time.time()

        # 全局错误延迟（对齐旧容器「瞬时错误太多」逻辑）：上一轮失败过多，
        # 本轮开头整体 sleep，让平台风控冷却
        with self._results_lock:
            round_failures = self._round_failures
        if round_failures >= cfg["global_error_threshold"] and cfg["global_error_delay"] > 0:
            logger.warning(
                f"上一轮取流失败 {round_failures} 次（阈值 {cfg['global_error_threshold']}），"
                f"整体延迟 {cfg['global_error_delay']}s 后继续"
            )
            await asyncio.sleep(cfg["global_error_delay"])
        self._reset_round_failures()

        self._maybe_adjust_concurrency()
        self._auto_stop_stale_monitors(cfg)

        base_interval = int(self.loop_time_seconds or 300)
        skipped_unsupported = 0
        for recording in self.recordings:
            if not recording.monitor_status or recording.is_recording:
                continue
            if recording.unsupported:
                skipped_unsupported += 1
                continue
            if recording.rec_id in self.active_recorders:
                continue

            # 快检：录制结束后的短窗口内不等完整间隔
            interval = base_interval
            if (
                recording.record_finished_at
                and cfg["post_record_recheck"] > 0
                and now - recording.record_finished_at <= cfg["post_record_recheck"] * 2
            ):
                interval = min(interval, cfg["post_record_recheck"])

            # 退避：连续失败间隔翻倍
            if cfg["backoff_enabled"] and recording.backoff_multiplier > 1:
                interval = interval * recording.backoff_multiplier

            # 新近度优先：活跃房间保住预算，冷房间让路
            if cfg["recency_priority_enabled"]:
                interval = interval * self._activity_tier_multiplier(recording, now)

            is_exceeded = utils.is_time_interval_exceeded(recording.detection_time, interval)
            if not recording.detection_time or is_exceeded or recording.next_check_after <= now:
                self.services.run_coro(self.check_if_live(recording))

        if skipped_unsupported:
            logger.info(f"本轮跳过 {skipped_unsupported} 个已标记不支持的任务")

    # 两档优先级：活跃房间（最近开播或开播频繁）每轮都查，其余每 2 轮一次
    MONITOR_HOT_RECENT_DAYS = 2    # 距上次开播在此天数内视为活跃
    MONITOR_HOT_FREQ_DAYS = 3      # 平均开播间隔不超过此天数视为开播频繁
    MONITOR_COLD_MULTIPLIER = 2    # 非活跃任务的检测间隔倍数（180s 轮 ≈ 6 分钟）

    def _activity_tier_multiplier(self, recording: Recording, now: float) -> int:
        """两档检测间隔：活跃 1×，冷门 2×。

        活跃 = 最近 MONITOR_HOT_RECENT_DAYS 天内播过（新近度），
        或平均开播间隔 ≤ MONITOR_HOT_FREQ_DAYS 天（频率，EMA 统计）。
        频率维度覆盖「按固定节奏回归的主播」：即使这次间隔略超 2 天，
        按其历史节奏仍应保持每轮检测。
        """
        llt = recording.last_live_time
        if llt and now - llt < self.MONITOR_HOT_RECENT_DAYS * 86400:
            return 1
        avg = recording.avg_live_interval
        if avg is not None and avg <= self.MONITOR_HOT_FREQ_DAYS * 86400:
            return 1
        return self.MONITOR_COLD_MULTIPLIER

    def _update_live_cadence(self, recording: Recording, now_ts: float) -> None:
        """未开播 → 开播过渡时更新开播节奏统计（EMA）。

        第 1 次开播无从计算间隔（last_live_time 初始为任务创建时刻的宽限
        起点），第 2 次起 gap = 两次开播时刻差，之后指数滑动平均。
        """
        recording.live_count += 1
        if recording.live_count >= 2 and recording.last_live_time:
            gap = now_ts - recording.last_live_time
            prev = recording.avg_live_interval
            recording.avg_live_interval = gap if prev is None else prev * 0.5 + gap * 0.5
        self.analytics.record_session(recording.rec_id, now_ts)
        self.analytics.maybe_flush()

    @staticmethod
    def _is_tracked(recording: Recording) -> bool:
        """任务是否仍在监控列表（排队期间可能已被删除）。"""
        return any(r.rec_id == recording.rec_id for r in GlobalRecordingState.recordings)

    def _jittered_interval(self, interval: float, jitter_ratio: float) -> float:
        if jitter_ratio <= 0:
            return interval
        spread = interval * jitter_ratio
        return interval + random.uniform(-spread, spread)

    def _on_check_failed(self, recording: Recording) -> None:
        """取流失败：累计连续失败、按退避策略排下次检查；超限标记 unsupported。"""
        cfg = self._monitor_config()
        recording.consecutive_failures += 1
        if cfg["backoff_enabled"]:
            recording.backoff_multiplier = min(
                recording.backoff_multiplier * 2, max(1, cfg["backoff_max"])
            )

        limit = cfg["unsupported_limit"]
        if limit and recording.consecutive_failures >= limit:
            recording.unsupported = True
            logger.warning(
                f"连续失败 {recording.consecutive_failures} 次，标记为不支持并停止轮询: "
                f"{recording.url}（编辑任务可重置）"
            )
            return

        base = int(self.loop_time_seconds or 300)
        interval = base * recording.backoff_multiplier
        recording.next_check_after = time.time() + max(1, self._jittered_interval(interval, cfg["jitter_ratio"]))

    def _on_check_succeeded(self, recording: Recording) -> None:
        """取流成功：清失败计数/退避/unsupported，按抖动排下次检查。"""
        cfg = self._monitor_config()
        was_unsupported = recording.unsupported
        recording.consecutive_failures = 0
        recording.backoff_multiplier = 1
        recording.unsupported = False
        if was_unsupported:
            logger.info(f"恢复轮询: {recording.url}")

        base = int(self.loop_time_seconds or 300)
        recording.next_check_after = time.time() + max(
            1, self._jittered_interval(base, cfg["jitter_ratio"])
        )

    # ── 监控池自动瘦身 ──────────────────────────────────

    def _auto_stop_stale_monitors(self, cfg: dict) -> None:
        """每轮清理监控池：直播间已失效的、连续 auto_stop_monitor_days 天
        未开播的任务自动停止监控（可通过重新开启监控恢复）。

        last_live_time 为空的任务（升级前已存在）首次观察到时初始化为当前
        时间——宽限期从此起算，避免重启/升级即误停存量任务。
        """
        days = cfg["auto_stop_monitor_days"]
        now = time.time()
        changed = False
        for recording in self.recordings:
            if not recording.monitor_status or recording.is_recording:
                continue
            if self._auto_stop_invalid(recording):
                changed = True
                continue
            if days <= 0:
                continue
            if recording.last_live_time is None:
                recording.last_live_time = now
                changed = True
                continue
            if now - recording.last_live_time > days * 86400:
                recording.monitor_status = False
                recording.status_info = RecordingStatus.STOPPED_MONITORING
                recording.display_title = f"[{self._['monitor_stopped']}] {recording.title}"
                recording.is_checking = False
                logger.warning(f"{days:g} 天未开播，自动停止监控: {recording.url}")
                self.services.broadcast_card_update(recording)
                changed = True
        if changed:
            self.services.run_coro(self.persist_recordings())

    def _auto_stop_invalid(self, recording: Recording) -> bool:
        """有效性缓存判定直播间已失效（抖音精确）→ 停止监控。返回是否有变更。"""
        entry = self.validity_cache.get(recording.rec_id)
        if not entry or entry.get("status") != room_validity.STATUS_INVALID:
            return False
        if entry.get("url") != recording.url:
            return False
        recording.monitor_status = False
        recording.status_info = RecordingStatus.STOPPED_MONITORING
        recording.display_title = f"[{self._['monitor_stopped']}] {recording.title}"
        recording.is_checking = False
        logger.warning(f"直播间已失效，自动停止监控: {recording.url}")
        self.services.broadcast_card_update(recording)
        return True

    _periodic_task_running = False

    @classmethod
    def is_periodic_task_running(cls):
        return cls._periodic_task_running

    @classmethod
    def set_periodic_task_running(cls, value=True):
        cls._periodic_task_running = value

    async def setup_periodic_live_check(self, interval: int = 180):
        """Set up a periodic task to check live status."""

        async def periodic_check():
            logger.info("Starting periodic live check background task")
            while True:
                immediate_check_on_startup = self.services.settings_config.user_config.get(
                    "check_live_on_browser_refresh", True
                )
                if immediate_check_on_startup:
                    await asyncio.sleep(interval)
                await self.check_free_space()
                if self.services.recording_enabled:
                    await self.check_all_live_status()
                self.analytics.maybe_flush()
                if not immediate_check_on_startup:
                    await asyncio.sleep(interval)

        if not RecordingManager.is_periodic_task_running():
            RecordingManager.set_periodic_task_running(True)
            self.periodic_task_started = True
            logger.info(f"Initializing periodic live check task with interval: {interval}s")
            asyncio.create_task(periodic_check())
        else:
            logger.info("Periodic live check task already running globally, skipping initialization")

    async def check_if_live(self, recording: Recording):
        """Check if the live stream is available, fetch stream data and update is_live status.

        in-flight 守卫：任务已有检查协程在排队/执行时跳过重复派发——节奏门
        使请求排队成为常态（任务多时一轮消化不完），没有守卫的话调度器每轮
        都会对排队中的任务再派协程，队列越滚越大、同一任务连发多次请求。
        """
        if recording.check_in_flight:
            logger.debug(f"Skip check_if_live, check already in flight: {recording.url}")
            return
        recording.check_in_flight = True
        try:
            await self._check_if_live_impl(recording)
        finally:
            recording.check_in_flight = False

    async def _check_if_live_impl(self, recording: Recording):
        recording.manually_stopped = False
        if recording.is_recording or recording.stopping_in_progress:
            logger.debug(f"Skip check_if_live because recording is busy: {recording.url}")
            return

        if recording.rec_id in self.active_recorders:
            logger.debug(f"Skip check_if_live because recorder is active: {recording.url}")
            return

        if not recording.monitor_status:
            recording.display_title = f"[{self._['monitor_stopped']}] {recording.title}"
            recording.status_info = RecordingStatus.STOPPED_MONITORING
            recording.is_checking = False
            self.services.broadcast_card_update(recording)
            return

        recording.detection_time = datetime.now().time()
        recording.is_checking = True

        if not recording.showed_checking_status:
            recording.status_info = RecordingStatus.STATUS_CHECKING
            recording.showed_checking_status = True
            self.services.broadcast_card_update(recording)

        if recording.scheduled_recording:
            scheduled_time_range_list = await self.get_scheduled_time_range(
                recording.scheduled_start_time, recording.monitor_hours
            )
            recording.scheduled_time_range = scheduled_time_range_list
            in_scheduled = False
            for scheduled_time_range in scheduled_time_range_list or []:
                in_scheduled = utils.is_current_time_within_range(scheduled_time_range)
                if in_scheduled:
                    break

            if not in_scheduled:
                recording.status_info = RecordingStatus.NOT_IN_SCHEDULED_CHECK
                recording.is_live = False
                recording.is_checking = False
                logger.info(f"Skip Detection: {recording.url} not in scheduled check range {scheduled_time_range_list}")
                self.services.broadcast_card_update(recording)
                return

        recording.status_info = RecordingStatus.STATUS_CHECKING
        platform, platform_key = get_platform_info(recording.url)

        if platform and platform_key and (recording.platform is None or recording.platform_key is None):
            recording.platform = platform
            recording.platform_key = platform_key
            self.services.run_coro(self.persist_recordings())

        # 设置里存的是 "Chinese"/"English"，与 zh_CN 比较恒不等导致中文用户也被换成英文平台名
        if self.settings.user_config.get("language") == "English":
            platform = platform_key

        output_dir = self.settings.get_video_save_path()
        await self.check_free_space(output_dir)
        if not self.services.recording_enabled:
            recording.is_checking = False
            recording.status_info = RecordingStatus.NOT_RECORDING_SPACE
            return
        recording_info = {
            "platform": platform,
            "platform_key": platform_key,
            "live_url": recording.url,
            "output_dir": output_dir,
            "segment_record": recording.segment_record,
            "segment_time": recording.segment_time,
            "save_format": recording.record_format,
            "quality": recording.quality,
            "video_bitrate": recording.video_bitrate,
        }

        recorder = LiveStreamRecorder(self.services, recording, recording_info)
        async with self._platform_slot(platform_key):
            # detection_time 以实际请求发起时刻为准：任务在节奏门前可能排队
            # 数轮，若沿用派发时刻，调度器会在检查完成后立刻误判超时重派
            recording.detection_time = datetime.now().time()
            stream_info = await recorder.fetch_stream()
            logger.info(f"Stream Data: {stream_info}")
        self._record_request_result(ok=bool(stream_info and stream_info.anchor_name), platform_key=platform_key)
        # 过期守卫：排队期间任务可能被停止监控、编辑 URL 或删除。旧检查的
        # 结果必须作废——否则会用旧房间数据覆盖任务，甚至对已停止监控/已
        # 删除的任务启动录制（节奏门把这段竞态窗口从毫秒级放大到了分钟级）
        if (
            not recording.monitor_status
            or recording.url != recording_info["live_url"]
            or not self._is_tracked(recording)
        ):
            logger.info(f"Discard stale check result: {recording.url}")
            recording.is_checking = False
            return
        if not stream_info or not stream_info.anchor_name:
            logger.error(f"Fetch stream data failed: {recording.url}")
            recording.is_checking = False
            recording.status_info = RecordingStatus.LIVE_STATUS_CHECK_ERROR
            self._on_check_failed(recording)
            if recording.monitor_status:
                self.services.broadcast_card_update(recording)
                self.services.broadcast_pubsub("update", recording)
            return

        self._on_check_succeeded(recording)
        if self.settings.user_config.get("remove_emojis"):
            stream_info.anchor_name = utils.clean_name(stream_info.anchor_name, self._["live_room"])

        if stream_info.is_live:
            now_ts = time.time()
            if not recording.is_live:
                self._update_live_cadence(recording, now_ts)
            recording.last_live_time = now_ts
            recording.live_title = stream_info.title
            # 空名（只填房间号新建的任务）或默认占位名都回填真实主播名
            if not recording.streamer_name.strip() or recording.streamer_name.strip() == self._["live_room"]:
                recording.streamer_name = stream_info.anchor_name
                self.services.run_coro(self.persist_recordings())
            recording.title = f"{recording.streamer_name} - {self._[recording.quality]}"
            recording.display_title = f"[{self._['is_live']}] {recording.title}"

            if not recording.is_live:
                recording.is_live = stream_info.is_live
                recording.notified_live_start = False
                recording.notified_live_end = False

            msg_manager = message_pusher.MessagePusher(self.settings)
            user_config = self.settings.user_config
            if (
                msg_manager.should_push_message(self.settings, recording, message_type="start")
                and not recording.notified_live_start
            ):
                push_content = self._["push_content"]
                begin_push_message_text = user_config.get("custom_stream_start_content")
                if begin_push_message_text:
                    push_content = begin_push_message_text

                push_at = datetime.today().strftime("%Y-%m-%d %H:%M:%S")
                push_content = (
                    push_content.replace("[room_name]", recording.streamer_name)
                    .replace("[time]", push_at)
                    .replace("[title]", recording.live_title or "None")
                )
                msg_title = user_config.get("custom_notification_title").strip()
                msg_title = msg_title or self._["status_notify"]

                BackgroundService.get_instance().add_task(msg_manager.push_messages_sync, msg_title, push_content)
                recording.notified_live_start = True

            if not recording.only_notify_no_record:
                recording.status_info = RecordingStatus.PREPARING_RECORDING
                recording.loop_time_seconds = self.loop_time_seconds
                self.start_update(recording)
                self.services.run_coro(recorder.start_recording(stream_info))
            else:
                if recording.notified_live_start:
                    notify_loop_time = user_config.get("notify_loop_time")
                    recording.loop_time_seconds = int(notify_loop_time or 600)
                else:
                    recording.loop_time_seconds = self.loop_time_seconds

                recording.cumulative_duration = timedelta()
                recording.last_duration = timedelta()
                recording.status_info = RecordingStatus.LIVE_BROADCASTING

        else:
            recording.is_recording = False
            if recording.is_live:
                recording.is_live = False
                asyncio.create_task(recorder.end_message_push())

            recording.status_info = RecordingStatus.MONITORING
            title = f"{stream_info.anchor_name or recording.streamer_name} - {self._[recording.quality]}"
            if (
                not recording.streamer_name.strip()
                or recording.streamer_name == self._["live_room"]
                or f"[{self._['is_live']}]" in recording.display_title
            ):
                recording.update(
                    {
                        "streamer_name": stream_info.anchor_name,
                        "title": title,
                        "display_title": title,
                    }
                )
                self.services.run_coro(self.persist_recordings())

        recording.is_checking = False
        self.services.broadcast_card_update(recording)
        self.services.broadcast_pubsub("update", recording)
        return

    @staticmethod
    def start_update(recording: Recording):
        """Start the recording process."""
        if recording.is_live and not recording.is_recording:
            # Reset cumulative and last durations for a fresh start
            recording.update(
                {
                    "cumulative_duration": timedelta(),
                    "last_duration": timedelta(),
                    "start_time": datetime.now(),
                    "is_recording": True,
                }
            )
            logger.info(f"Started recording for {recording.title}")

    def stop_recording(self, recording: Recording, manually_stopped: bool = True):
        """Stop the recording process."""
        recording.is_live = False
        if recording.is_recording:
            recording.stopping_in_progress = True

            logger.info(f"Trying to stop recorder for {recording.rec_id}, title: {recording.title}")
            logger.debug(f"Active recorders: {list(self.active_recorders.keys())}")

            if recording.rec_id in self.active_recorders:
                recorder = self.active_recorders[recording.rec_id]
                logger.debug(f"Found recorder instance - id: {id(recorder)}")
                recorder.request_stop()
                logger.info(f"Requested stop for recorder: {recording.rec_id}")
            else:
                logger.warning(f"No active recorder found for {recording.rec_id}, cannot request stop")
                recording.force_stop = True
                logger.info(f"Set force_stop=True for recording: {recording.rec_id}")

            if recording.start_time is not None:
                elapsed = datetime.now() - recording.start_time
                # Add the elapsed time to the cumulative duration.
                recording.cumulative_duration += elapsed
                # Update the last recorded duration.
                recording.last_duration = recording.cumulative_duration
            recording.start_time = None
            recording.is_recording = False
            recording.manually_stopped = manually_stopped
            recording.status_info = RecordingStatus.NOT_RECORDING
            logger.info(f"Stopped recording for {recording.title}")

            self.services.run_coro(self._reset_stopping_flag(recording))

    def get_duration(self, recording: Recording):
        """Get the duration of the current recording session in a formatted string."""
        if recording.is_recording and recording.start_time is not None:
            elapsed = datetime.now() - recording.start_time
            # If recording, add the current session time.
            total_duration = recording.cumulative_duration + elapsed
            return self._["recorded"] + " " + str(total_duration).split(".")[0]
        else:
            # If stopped, show the last recorded total duration.
            total_duration = recording.last_duration
            return str(total_duration).split(".")[0]

    async def delete_recording_cards(self, recordings: list[Recording]):
        self.services.broadcast_card_remove(recordings)
        self.services.broadcast_pubsub("delete", recordings)
        await self.remove_recordings(recordings)

    async def check_free_space(self, output_dir: str | None = None):
        disk_space_limit = float(self.settings.user_config.get("recording_space_threshold") or 0)
        output_dir = output_dir or self.settings.get_video_save_path()
        if utils.check_disk_capacity(output_dir) < disk_space_limit:
            self.services.recording_enabled = False
            logger.error(f"Disk space remaining is below {disk_space_limit} GB. Recording function disabled")
            self.services.broadcast_snack(
                self._["not_disk_space_tip"],
                duration=86400,
                show_close_icon=True,
            )

        else:
            self.services.recording_enabled = True

    @staticmethod
    async def get_scheduled_time_range(scheduled_start_time, monitor_hours) -> list | None:
        if not scheduled_start_time:
            return None
        scheduled_time_range_list = []
        monitor_hours_list = str(monitor_hours).split(",") if monitor_hours else []
        for index, start_time in enumerate(str(scheduled_start_time).split(",")):
            try:
                hours = monitor_hours_list[index] if index < len(monitor_hours_list) else ""
                if start_time and hours:
                    end_time = utils.add_hours_to_time(start_time, float(hours or 5))
                    scheduled_time_range = f"{start_time}~{end_time}"
                    scheduled_time_range_list.append(scheduled_time_range)
            except Exception:
                pass
        return scheduled_time_range_list

    @staticmethod
    async def _reset_stopping_flag(recording: Recording):
        recording.stopping_in_progress = False
        logger.debug(f"Reset stopping_in_progress flag for recording: {recording.rec_id}")
