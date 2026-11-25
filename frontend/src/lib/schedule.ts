/**
 * 「每日定时监听」的时间段换算。
 *
 * 后端存的是 `scheduled_start_time` + `monitor_hours`（时长），运行期才拼成
 * `HH:MM:SS~HH:MM:SS`。界面上给用户填的是**开始 / 结束**（更直观，不用心算），
 * 这里负责两边互转：
 * - 载入任务：`end = start + hours`
 * - 保存任务：`hours = end - start`（结束早于开始按次日算，如 23:00 → 02:00 = 3h）
 *
 * 全部用「当天秒数」算，不碰 `Date`，避免时区/夏令时干扰。
 */

const pad2 = (n: number) => String(n).padStart(2, "0")

/** `HH:MM` / `HH:MM:SS` → 当天秒数；非法返回 null */
export function parseClock(value: string): number | null {
    const m = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec((value ?? "").trim())
    if (!m) return null
    const h = Number(m[1])
    const min = Number(m[2])
    const sec = Number(m[3] ?? 0)
    if (h > 23 || min > 59 || sec > 59) return null
    return h * 3600 + min * 60 + sec
}

/** 当天秒数 → `HH:MM:SS`（按 24h 取模） */
export function formatClock(seconds: number): string {
    const s = ((Math.round(seconds) % 86400) + 86400) % 86400
    return `${pad2(Math.floor(s / 3600))}:${pad2(Math.floor((s % 3600) / 60))}:${pad2(s % 60)}`
}

/** 开始时间 + 时长（小时，可小数）→ 结束时间 `HH:MM:SS`；算不出来返回 "" */
export function endFromHours(start: string, hours: string | number | null | undefined): string {
    const startSec = parseClock(start)
    const h = Number(hours)
    if (startSec == null || !Number.isFinite(h) || h <= 0) return ""
    return formatClock(startSec + h * 3600)
}

/**
 * 开始~结束 → 小时数（保留到秒的精度，用于回写 `monitor_hours`）。
 * 结束早于开始 = 跨零点按次日算；两者相等返回 null（等于不监控，应当拦掉）。
 */
export function hoursFromRange(start: string, end: string): number | null {
    const startSec = parseClock(start)
    const endSec = parseClock(end)
    if (startSec == null || endSec == null || startSec === endSec) return null
    const diff = (endSec - startSec + 86400) % 86400
    // 6 位小数 ≈ 1.8ms 误差，后端 start + hours 能精确还原到同一秒
    return Number((diff / 3600).toFixed(6))
}

/** 结束时间是否跨到次日（用于显示「次日」提示） */
export function crossesMidnight(start: string, end: string): boolean {
    const startSec = parseClock(start)
    const endSec = parseClock(end)
    return startSec != null && endSec != null && endSec < startSec
}

/** 给界面显示用：0.5h → "0.5"，3h → "3" */
export function formatHours(hours: number): string {
    return String(Number(hours.toFixed(2)))
}
