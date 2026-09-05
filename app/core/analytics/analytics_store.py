"""录制分析汇总存储：按天聚合、按月分文件持久化（只存汇总，无事件明细）。

数据结构：
- 月度文件 config/analytics/analytics_YYYY-MM.json
  daily[日期]["t"][rec_id] = [场次, 录制秒数, 文件数]
  daily[日期]["p"][platform_key] = [检测次数, 失败次数]
- config/analytics/hours.json: hours[rec_id] = [24 个整数]（累计开播小时分布）

增长控制：每天每任务约 60 字节，170 任务 ≈ 0.3MB/月；当月文件防抖重写，
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


def _date_str(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _month_str(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m")


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

    # ── 埋点 ────────────────────────────────────────────

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

    def record_session(self, rec_id: str, ts: float) -> None:
        """开播一场：场次 +1，小时直方图 +1。"""
        with self._lock:
            daily = self._month_bucket(ts)
            day = daily.setdefault(_date_str(ts), {"t": {}, "p": {}})
            entry = day["t"].setdefault(rec_id, [0, 0.0, 0])
            entry[0] += 1
            hour = datetime.fromtimestamp(ts).hour
            hours = self._hours.setdefault(rec_id, [0] * HOURS_BUCKET_COUNT)
            hours[hour] += 1
            self._dirty = True

    def record_segment(self, rec_id: str, start_ts: float, duration_seconds: float, files: int) -> None:
        """一段录制结束：时长与文件数计入开始日期。"""
        if duration_seconds <= 0 and files <= 0:
            return
        with self._lock:
            daily = self._month_bucket(start_ts)
            day = daily.setdefault(_date_str(start_ts), {"t": {}, "p": {}})
            entry = day["t"].setdefault(rec_id, [0, 0.0, 0])
            entry[1] += max(0.0, duration_seconds)
            entry[2] += max(0, files)
            self._dirty = True

    def record_check(self, platform_key: str, ok: bool, ts: float) -> None:
        """一次直播状态检测（成功/失败）。"""
        with self._lock:
            daily = self._month_bucket(ts)
            day = daily.setdefault(_date_str(ts), {"t": {}, "p": {}})
            entry = day["p"].setdefault(platform_key, [0, 0])
            entry[0] += 1
            if not ok:
                entry[1] += 1
            self._dirty = True

    # ── 查询 ────────────────────────────────────────────

    def read_daily_range(self, start_date: str, end_date: str) -> dict:
        """合并读取日期区间内的日聚合：{日期: {"t": {...}, "p": {...}}}。

        跨月时从磁盘读对应月度文件；当月部分用驻留数据（含未落盘更新）。
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
                        merged[date] = buckets
            return merged

    def read_hours(self) -> dict:
        with self._lock:
            self._ensure_loaded()
            return dict(self._hours)

    @staticmethod
    def _iter_dates(start_date: str, end_date: str):
        """按天枚举日期字符串（含首尾），用于推导涉及的月份。"""
        from datetime import timedelta

        current = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        while current <= end:
            yield current.strftime("%Y-%m-%d")
            current += timedelta(days=1)
