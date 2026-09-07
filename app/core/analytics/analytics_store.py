"""录制分析汇总存储：按天聚合、按月分文件持久化（只存汇总，无事件明细）。

数据结构：
- 月度文件 config/analytics/analytics_YYYY-MM.json
  daily[日期]["t"][rec_id] = [11 槽，见 T_* 常量]（按任务）
  daily[日期]["p"][platform_key] = [6 槽，见 P_* 常量]（按平台）
- config/analytics/hours.json: hours[rec_id] = [24 个整数]（累计开播小时分布）

槽位「只增不改」：新字段一律追加到数组尾部，所有读路径统一经 _pad() 补零，
因此旧月度文件无需迁移——新槽位在历史日期上恒为 0。位置数组而非字典是为了
控体积（见下方估算），代价是可读性，用 T_*/P_* 常量索引换回来。

增长控制：每天每任务约 140 字节，170 任务 ≈ 0.7MB/月；当月文件防抖重写，
历史月份只读。跨天/跨月自动切换桶，旧文件永不重写。
"""
import json
import os
import threading
import time
from datetime import datetime

from ...utils.logger import logger

FLUSH_DEBOUNCE_SECONDS = 60
HOURS_BUCKET_COUNT = 24

# ── t 桶（按任务）槽位 ──────────────────────────────────
T_SESSIONS = 0       # 开播场次
T_SECONDS = 1        # 录制时长：ffmpeg 进程壁钟秒数，非媒体时长
T_FILES = 2          # 产出文件数
T_RAW_BYTES = 3      # ffmpeg 毛产出字节（转码/识别前）
T_STARTS = 4         # 实际派发录制的次数
T_ABORTS = 5         # ffmpeg 异常退出次数（return_code 不在安全集内）
T_NOTIFY_ONLY = 6    # 开播但配置为「只通知不录制」的场次
T_CHECKS = 7         # 按任务的直播状态检测次数
T_CHECK_FAILS = 8    # 按任务的检测失败次数
T_POSE_OUT = 9       # 人体识别产物字节
T_POSE_DEL = 10      # 人体识别删掉的原视频字节
T_WIDTH = 11

# ── p 桶（按平台）槽位 ──────────────────────────────────
P_CHECKS = 0
P_FAILURES = 1
P_F_TRANSIENT = 2     # 首次失败（可能只是偶发抖动）
P_F_REPEATED = 3      # 连续失败中，未到 unsupported 阈值
P_F_UNSUPPORTED = 4   # 连续失败超限，已标记不支持并停止轮询
P_F_INVALID = 5       # 有效性缓存已判定房间失效
P_WIDTH = 6

# 失败原因 → p 桶槽位。归因是「状态派生」的：handler 被 @trace_error_decorator
# 包裹后拿不到异常细节，精确判定需额外发请求（吃平台风控预算），故不做。
FAILURE_REASON_SLOTS = {
    "transient": P_F_TRANSIENT,
    "repeated": P_F_REPEATED,
    "unsupported": P_F_UNSUPPORTED,
    "invalid": P_F_INVALID,
}


def _date_str(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _month_str(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m")


def _pad(entry: list, width: int) -> list:
    """就地补零到指定宽度，返回同一个 list 对象。

    旧月度文件里的短数组（升级前只有 3/2 槽）统一在读路径经此补齐，
    调用方因此可以无条件按 T_*/P_* 常量索引。
    """
    if len(entry) < width:
        entry.extend([0] * (width - len(entry)))
    return entry


def _new_t() -> list:
    entry = [0] * T_WIDTH
    entry[T_SECONDS] = 0.0
    return entry


def _new_p() -> list:
    return [0] * P_WIDTH


class AnalyticsStore:
    def __init__(self, analytics_dir: str):
        self.analytics_dir = analytics_dir
        # RLock：record_* 持锁期间跨月会经 _month_bucket → flush 再次加锁
        self._lock = threading.RLock()
        self._daily: dict = {}   # 仅驻留当月：daily[日期]["t"/"p"][key] = [...]
        self._month: str | None = None
        self._hours: dict = {}
        self._dirty = False
        self._last_flush = 0.0
        self._loaded = False

    # ── 持久化 ──────────────────────────────────────────

    def _month_path(self, month: str) -> str:
        return os.path.join(self.analytics_dir, f"analytics_{month}.json")

    @property
    def _hours_path(self) -> str:
        return os.path.join(self.analytics_dir, "hours.json")

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        os.makedirs(self.analytics_dir, exist_ok=True)
        month = _month_str(time.time())
        self._month = month
        self._daily = {}
        try:
            with open(self._month_path(month), encoding="utf-8") as f:
                data = json.load(f)
            self._daily = data.get("daily", {})
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"Failed to load analytics month file: {e}")
        try:
            with open(self._hours_path, encoding="utf-8") as f:
                self._hours = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self._hours = {}
        self._loaded = True

    def _write_json(self, path: str, data: dict) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)

    def flush(self) -> None:
        """落盘当月文件 + 小时直方图（仅在脏时写）。"""
        with self._lock:
            if not self._dirty or not self._loaded:
                return
            try:
                os.makedirs(self.analytics_dir, exist_ok=True)
                self._write_json(self._month_path(self._month), {"daily": self._daily})
                self._write_json(self._hours_path, self._hours)
                self._dirty = False
                self._last_flush = time.time()
            except Exception as e:
                logger.warning(f"Failed to flush analytics: {e}")

    def maybe_flush(self) -> None:
        """防抖落盘：距上次落盘超过 FLUSH_DEBOUNCE_SECONDS 才真正写。"""
        if self._dirty and time.time() - self._last_flush >= FLUSH_DEBOUNCE_SECONDS:
            self.flush()

    def purge_rec(self, rec_id: str) -> None:
        """任务删除后清掉该 rec_id 的汇总数据（驻留当月、hours、磁盘各月度文件）。

        历史月份文件「只读」惯例在此为删除场景破例：否则被删任务会以
        rec_id 前缀的名字永久残留在排行/分布视图中。
        """
        with self._lock:
            self._ensure_loaded()
            removed = False
            for day in self._daily.values():
                if rec_id in day.get("t", {}):
                    del day["t"][rec_id]
                    removed = True
            if self._hours.pop(rec_id, None) is not None:
                removed = True
            if not removed:
                return
            self._dirty = True
            try:
                if os.path.isdir(self.analytics_dir):
                    for name in os.listdir(self.analytics_dir):
                        if not (name.startswith("analytics_") and name.endswith(".json")):
                            continue
                        month = name[len("analytics_"):-len(".json")]
                        if month == self._month:
                            continue  # 驻留月随下方 flush 覆盖写入
                        path = self._month_path(month)
                        try:
                            with open(path, encoding="utf-8") as f:
                                data = json.load(f)
                        except (FileNotFoundError, json.JSONDecodeError, OSError):
                            continue
                        changed = False
                        for day in data.get("daily", {}).values():
                            if rec_id in day.get("t", {}):
                                del day["t"][rec_id]
                                changed = True
                        if changed:
                            try:
                                self._write_json(path, data)
                            except OSError as e:
                                logger.warning(f"Failed to purge analytics month file: {e}")
                self.flush()
            except Exception as e:
                logger.warning(f"Failed to purge analytics for {rec_id}: {e}")

    # ── 桶定位 ──────────────────────────────────────────

    def _month_bucket(self, ts: float) -> dict:
        """取某时刻的日聚合桶；跨月时把驻留数据落盘后切换到新月。"""
        self._ensure_loaded()
        month = _month_str(ts)
        if month != self._month:
            self._dirty = True
            self.flush()  # 旧月收尾（flush 内部用当前 _month）
            self._month = month
            self._daily = {}
        return self._daily

    def _day_bucket(self, ts: float) -> dict:
        """取某时刻的当日桶，确保 t/p 两个子桶都存在（旧文件可能缺键）。"""
        day = self._month_bucket(ts).setdefault(_date_str(ts), {"t": {}, "p": {}})
        day.setdefault("t", {})
        day.setdefault("p", {})
        return day

    def _t_entry(self, ts: float, rec_id: str) -> list:
        return _pad(self._day_bucket(ts)["t"].setdefault(rec_id, _new_t()), T_WIDTH)

    # ── 埋点 ────────────────────────────────────────────

    def record_session(self, rec_id: str, ts: float, notify_only: bool = False) -> None:
        """开播一场：场次 +1，小时直方图 +1。

        notify_only：任务配置为「只通知不录制」，这类场次不算漏录，
        计算捕获率时要从分母里剔除。
        """
        with self._lock:
            entry = self._t_entry(ts, rec_id)
            entry[T_SESSIONS] += 1
            if notify_only:
                entry[T_NOTIFY_ONLY] += 1
            hour = datetime.fromtimestamp(ts).hour
            hours = _pad(self._hours.setdefault(rec_id, [0] * HOURS_BUCKET_COUNT), HOURS_BUCKET_COUNT)
            hours[hour] += 1
            self._dirty = True

    def record_record_start(self, rec_id: str, ts: float) -> None:
        """实际派发了一次录制（捕获率的分子）。"""
        with self._lock:
            self._t_entry(ts, rec_id)[T_STARTS] += 1
            self._dirty = True

    def record_segment(
        self,
        rec_id: str,
        start_ts: float,
        duration_seconds: float,
        files: int,
        raw_bytes: int = 0,
        aborted: bool = False,
    ) -> None:
        """一段录制结束：时长、文件数、字节数与中断标记计入开始日期。"""
        if duration_seconds <= 0 and files <= 0 and raw_bytes <= 0 and not aborted:
            return
        with self._lock:
            entry = self._t_entry(start_ts, rec_id)
            entry[T_SECONDS] += max(0.0, duration_seconds)
            entry[T_FILES] += max(0, files)
            entry[T_RAW_BYTES] += max(0, raw_bytes)
            if aborted:
                entry[T_ABORTS] += 1
            self._dirty = True

    def record_pose(self, rec_id: str, ts: float, out_bytes: int = 0, del_bytes: int = 0) -> None:
        """一次人体识别的产物字节与释放字节（原视频被删时）。"""
        if out_bytes <= 0 and del_bytes <= 0:
            return
        with self._lock:
            entry = self._t_entry(ts, rec_id)
            entry[T_POSE_OUT] += max(0, out_bytes)
            entry[T_POSE_DEL] += max(0, del_bytes)
            self._dirty = True

    def record_check(
        self,
        platform_key: str,
        ok: bool,
        ts: float,
        rec_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        """一次直播状态检测：按平台记总数与失败原因分档，按任务记成败计数。"""
        with self._lock:
            day = self._day_bucket(ts)
            entry = _pad(day["p"].setdefault(platform_key, _new_p()), P_WIDTH)
            entry[P_CHECKS] += 1
            if not ok:
                entry[P_FAILURES] += 1
                slot = FAILURE_REASON_SLOTS.get(reason)
                if slot is not None:
                    entry[slot] += 1
            if rec_id:
                t_entry = _pad(day["t"].setdefault(rec_id, _new_t()), T_WIDTH)
                t_entry[T_CHECKS] += 1
                if not ok:
                    t_entry[T_CHECK_FAILS] += 1
            self._dirty = True

    # ── 查询 ────────────────────────────────────────────

    def read_daily_range(self, start_date: str, end_date: str) -> dict:
        """合并读取日期区间内的日聚合：{日期: {"t": {...}, "p": {...}}}。

        跨月时从磁盘读对应月度文件；当月部分用驻留数据（含未落盘更新）。
        返回前统一 _pad，调用方可无条件按 T_*/P_* 常量索引。
        """
        with self._lock:
            self._ensure_loaded()
            months = sorted({_m[:7] for _m in self._iter_dates(start_date, end_date)})
            merged: dict = {}
            for month in months:
                if month == self._month:
                    month_daily = self._daily
                else:
                    try:
                        with open(self._month_path(month), encoding="utf-8") as f:
                            month_daily = json.load(f).get("daily", {})
                    except (FileNotFoundError, json.JSONDecodeError, OSError):
                        continue
                for date, buckets in month_daily.items():
                    if start_date <= date <= end_date:
                        for entry in (buckets.get("t") or {}).values():
                            _pad(entry, T_WIDTH)
                        for entry in (buckets.get("p") or {}).values():
                            _pad(entry, P_WIDTH)
                        merged[date] = buckets
            return merged

    def read_hours(self) -> dict:
        with self._lock:
            self._ensure_loaded()
            return {rid: _pad(list(hours), HOURS_BUCKET_COUNT) for rid, hours in self._hours.items()}

    @staticmethod
    def _iter_dates(start_date: str, end_date: str):
        """按天枚举日期字符串（含首尾），用于推导涉及的月份。"""
        from datetime import timedelta

        current = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        while current <= end:
            yield current.strftime("%Y-%m-%d")
            current += timedelta(days=1)
