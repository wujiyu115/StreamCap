import { useQuery } from "@tanstack/react-query"
import {
    AlertTriangle,
    CalendarDays,
    Clock,
    Database,
    Film,
    HardDrive,
    Loader2,
    Radio,
    Scissors,
    Target,
    TrendingDown,
    TrendingUp,
    Users,
} from "lucide-react"
import { memo, useState } from "react"
import { analyticsApi } from "@/api"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useI18n } from "@/i18n"
import type { AnalyticsOverview } from "@/api/types"

const DAY_OPTIONS = [7, 30, 90]

function fmtDuration(seconds: number): string {
    const h = Math.floor(seconds / 3600)
    const m = Math.round((seconds % 3600) / 60)
    if (h === 0 && m === 0) return "0" + " " + "min"
    if (h === 0) return `${m} min`
    return `${h} h ${m} min`
}

function fmtDurationI18n(seconds: number, t: (k: string) => string): string {
    const h = Math.floor(seconds / 3600)
    const m = Math.round((seconds % 3600) / 60)
    if (h === 0 && m === 0) return `0 ${t("analytics.minShort")}`
    if (h === 0) return `${m} ${t("analytics.minShort")}`
    return `${h} ${t("analytics.hourShort")} ${m} ${t("analytics.minShort")}`
}

function fmtDateShort(dateStr: string): string {
    return dateStr.slice(5).replace("-", "/")
}

function StatCard({ icon, label, value, sub }: { icon: React.ReactNode; label: string; value: string; sub?: React.ReactNode }) {
    return (
        <Card>
            <CardContent className="flex items-center gap-3 p-4">
                <div className="grid h-10 w-10 shrink-0 place-items-center rounded-md bg-primary/10 text-primary">
                    {icon}
                </div>
                <div className="min-w-0">
                    <div className="truncate text-xl font-bold leading-tight">{value}</div>
                    <div className="flex items-center gap-1 truncate text-xs text-muted-foreground">
                        {label}
                        {sub}
                    </div>
                </div>
            </CardContent>
        </Card>
    )
}

function BarList({
    rows,
    emptyText,
}: {
    rows: { label: string; value: number; valueText: string; sub?: string }[]
    emptyText: string
}) {
    if (rows.length === 0) return <div className="py-6 text-center text-sm text-muted-foreground">{emptyText}</div>
    const max = Math.max(...rows.map((r) => r.value), 1)
    return (
        <div className="space-y-2">
            {rows.map((r, i) => (
                <div key={i} className="space-y-0.5">
                    <div className="flex items-baseline justify-between gap-2 text-sm">
                        <span className="min-w-0 truncate" title={r.label}>
                            {r.label}
                        </span>
                        <span className="shrink-0 tabular-nums text-muted-foreground">{r.valueText}</span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                        <div
                            className="h-full rounded-full bg-primary/70"
                            style={{ width: `${Math.max(4, (r.value / max) * 100)}%` }}
                        />
                    </div>
                    {r.sub && <div className="text-xs text-muted-foreground">{r.sub}</div>}
                </div>
            ))}
        </div>
    )
}

/**
 * 主播开播分布行：约 50 个 DOM 节点/行，行数≈任务数（生产 150+），
 * 全量渲染进 max-h-96 滚动容器会让移动端滚动到这里时卡顿——
 * memo 让轮询刷新时未变化的行跳过 vdom 重建，content-visibility 让
 * 视口外的行跳过 layout/paint（首屏与滚动都只处理可见行）。
 */
const StreamerHoursRow = memo(function StreamerHoursRow({
    name,
    hours,
    total,
    peakHour,
    peakLabel,
    peakDesc,
}: {
    name: string
    hours: number[]
    total: number
    peakHour: number
    peakLabel: string
    peakDesc: string
}) {
    const max = Math.max(...hours, 1)
    return (
        <div
            className="flex items-center gap-3 [contain-intrinsic-size:auto_32px] [content-visibility:auto]"
        >
            <div className="w-24 shrink-0 truncate text-sm sm:w-32" title={`${name} · ${total}`}>
                {name}
            </div>
            <div className="flex h-8 min-w-0 flex-1 items-end gap-px">
                {hours.map((count, hour) => (
                    <div
                        key={hour}
                        className="group relative h-full flex-1"
                        title={`${hour}:00–${hour + 1}:00 · ${count}`}
                    >
                        {/* 0 场次渲染最小高度细线（保留整点刻度感） */}
                        <div
                            className={`absolute bottom-0 w-full rounded-t ${
                                hour === peakHour ? "bg-blue-500" : "bg-blue-500/35 group-hover:bg-blue-500/70"
                            }`}
                            style={{ height: `${Math.max(3, (count / max) * 100)}%` }}
                        />
                    </div>
                ))}
            </div>
            <Badge variant="secondary" className="w-20 shrink-0 justify-center text-xs" title={peakDesc}>
                {peakLabel}
            </Badge>
        </div>
    )
})

function fmtBytes(bytes: number): string {
    const units = ["KB", "MB", "GB", "TB"]
    if (bytes < 1024) return `${bytes} B`
    let size = bytes / 1024
    for (const unit of units) {
        if (size < 1024 || unit === "TB") return `${size.toFixed(1)} ${unit}`
        size /= 1024
    }
    return `${size.toFixed(1)} TB`
}

function fmtClock(ts: number | null): string {
    if (!ts) return "-"
    return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
}

const FAILURE_REASONS = ["transient", "repeated", "unsupported", "invalid"] as const

function reasonLabel(reason: string, t: (k: string) => string): string {
    return t(`analytics.reason_${reason}`)
}

export default function AnalyticsPage() {
    const { t } = useI18n()
    const [days, setDays] = useState(30)
    const [trendMetric, setTrendMetric] = useState<"sessions" | "bytes">("sessions")
    const { data, isLoading } = useQuery({
        queryKey: ["analytics-overview", days],
        queryFn: () => analyticsApi.overview(days),
        refetchInterval: 60_000,
    })

    if (isLoading || !data) {
        return (
            <div className="flex min-h-0 flex-1 items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
        )
    }

    const {
        summary,
        trend,
        rankings,
        idle,
        never_recorded,
        histogram,
        streamer_hours,
        platform_checks,
        failure_reasons,
        pose,
        disk,
        storage,
    } = data
    const trendValue = (d: (typeof trend)[number]) => (trendMetric === "bytes" ? d.bytes : d.sessions)
    const maxTrend = Math.max(...trend.map(trendValue), 1)
    const maxHistogram = Math.max(...histogram, 1)
    const changePct = summary.sessions_change_pct
    const failureTotal = FAILURE_REASONS.reduce((sum, r) => sum + failure_reasons[r], 0)
    const poseSaved = pose.deleted_bytes - pose.output_bytes

    return (
        <div className="flex min-h-full flex-col space-y-4">
            {/* 标题 + 时间范围 */}
            <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-xl font-bold">{t("analytics.title")}</h1>
                <div className="ml-auto flex gap-0.5 rounded-md border p-0.5">
                    {DAY_OPTIONS.map((d) => (
                        <button
                            key={d}
                            className={`rounded px-3 py-1 text-sm transition-colors ${
                                days === d ? "bg-primary text-primary-foreground" : "text-muted-foreground"
                            }`}
                            onClick={() => setDays(d)}
                        >
                            {t("analytics.daysOption").replace("{n}", String(d))}
                        </button>
                    ))}
                </div>
            </div>

            {/* 汇总卡片 */}
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
                <StatCard
                    icon={<Film className="h-5 w-5" />}
                    label={t("analytics.sessionsLabel")}
                    value={String(summary.sessions)}
                    sub={
                        changePct !== null ? (
                            <Badge variant={changePct >= 0 ? "secondary" : "outline"} className="gap-0.5 px-1 py-0">
                                {changePct >= 0 ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
                                {changePct >= 0 ? "+" : ""}
                                {changePct}%
                            </Badge>
                        ) : undefined
                    }
                />
                <StatCard
                    icon={<Clock className="h-5 w-5" />}
                    label={t("analytics.durationLabel")}
                    value={fmtDurationI18n(summary.seconds, t)}
                    sub={
                        <span className="cursor-help" title={t("analytics.durationHint")}>
                            ⓘ
                        </span>
                    }
                />
                <StatCard
                    icon={<CalendarDays className="h-5 w-5" />}
                    label={t("analytics.filesLabel")}
                    value={String(summary.files)}
                />
                <StatCard
                    icon={<Database className="h-5 w-5" />}
                    label={t("analytics.sizeLabel")}
                    value={fmtBytes(summary.bytes)}
                />
                <StatCard
                    icon={<Target className="h-5 w-5" />}
                    label={t("analytics.coverageLabel")}
                    value={
                        summary.record_coverage === null
                            ? "-"
                            : `${(summary.record_coverage * 100).toFixed(0)}%`
                    }
                    sub={
                        <span
                            className="cursor-help"
                            title={[
                                t("analytics.coverageDetail")
                                    .replace("{starts}", String(summary.starts))
                                    .replace("{sessions}", String(summary.recordable_sessions)),
                                t("analytics.notifyOnlyDetail").replace("{n}", String(summary.notify_only)),
                                t("analytics.abortDetail")
                                    .replace("{aborts}", String(summary.aborts))
                                    .replace("{starts}", String(summary.starts)),
                            ].join("\n")}
                        >
                            ⓘ
                        </span>
                    }
                />
                <StatCard
                    icon={<Users className="h-5 w-5" />}
                    label={t("analytics.activeAnchorsLabel")}
                    value={`${summary.active_anchors}/${summary.monitoring}`}
                />
            </div>

            <>
                {/* 磁盘水位 + 识别产出 */}
                <div className="grid gap-4 lg:grid-cols-2">
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-1.5 text-base">
                                <HardDrive className="h-4 w-4" />
                                {t("analytics.diskTitle")}
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-2 text-sm">
                            {disk.total_bytes === null || disk.free_bytes === null ? (
                                <div className="py-4 text-center text-sm text-muted-foreground">
                                    {t("analytics.diskUnknown")}
                                </div>
                            ) : (
                                <>
                                    <div className="h-2 overflow-hidden rounded-full bg-muted">
                                        <div
                                            className={`h-full rounded-full ${
                                                disk.free_bytes / disk.total_bytes < 0.1 ? "bg-destructive" : "bg-primary/70"
                                            }`}
                                            style={{
                                                width: `${Math.min(100, ((disk.total_bytes - disk.free_bytes) / disk.total_bytes) * 100)}%`,
                                            }}
                                        />
                                    </div>
                                    <div className="flex justify-between text-xs text-muted-foreground">
                                        <span>
                                            {t("analytics.diskFree")
                                                .replace("{free}", fmtBytes(disk.free_bytes))
                                                .replace("{total}", fmtBytes(disk.total_bytes))}
                                        </span>
                                        {disk.days_left_estimate !== null && (
                                            <span title={t("analytics.diskPerDay").replace("{size}", fmtBytes(disk.bytes_per_day))}>
                                                {t("analytics.diskDaysLeft").replace("{d}", String(disk.days_left_estimate))}
                                            </span>
                                        )}
                                    </div>
                                </>
                            )}
                            <div className="border-t pt-2 text-xs text-muted-foreground" title={disk.path}>
                                {t("analytics.diskRecordings")
                                    .replace("{size}", fmtBytes(disk.recordings_bytes))
                                    .replace("{n}", String(disk.recordings_files))}
                                <span className="ml-1">
                                    · {t("analytics.diskSampled").replace("{time}", fmtClock(disk.sampled_at))}
                                </span>
                            </div>
                            {storage.files.length > 0 && (
                                <div
                                    className="text-xs text-muted-foreground"
                                    title={storage.files.map((f) => `${f.name}: ${fmtBytes(f.bytes)}`).join("\n")}
                                >
                                    {t("analytics.storageSize")
                                        .replace("{size}", fmtBytes(storage.total_bytes))
                                        .replace("{n}", String(storage.files.length))}
                                </div>
                            )}
                        </CardContent>
                    </Card>
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-1.5 text-base">
                                <Scissors className="h-4 w-4" />
                                {t("analytics.poseTitle")}
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-2 text-sm">
                            {pose.output_bytes === 0 && pose.deleted_bytes === 0 ? (
                                <div className="py-4 text-center text-sm text-muted-foreground">
                                    {t("analytics.poseEmpty")}
                                </div>
                            ) : (
                                <>
                                    <div className="flex items-center justify-between gap-2">
                                        <span className="text-muted-foreground">{t("analytics.poseOutput")}</span>
                                        <span className="tabular-nums">{fmtBytes(pose.output_bytes)}</span>
                                    </div>
                                    <div className="flex items-center justify-between gap-2">
                                        <span className="text-muted-foreground">{t("analytics.poseDeleted")}</span>
                                        <span className="tabular-nums">{fmtBytes(pose.deleted_bytes)}</span>
                                    </div>
                                    <div className="flex items-center justify-between gap-2 border-t pt-2">
                                        <span className="text-muted-foreground">{t("analytics.poseSaved")}</span>
                                        <span className="tabular-nums">
                                            {poseSaved >= 0 ? "" : "-"}
                                            {fmtBytes(Math.abs(poseSaved))}
                                        </span>
                                    </div>
                                </>
                            )}
                            <div className="text-xs text-muted-foreground">{t("analytics.poseHint")}</div>
                        </CardContent>
                    </Card>
                </div>

                {/* 趋势：每日场次 / 每日体积 */}
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
                        <CardTitle className="text-base">
                            {t(trendMetric === "bytes" ? "analytics.trendBytesTitle" : "analytics.trendTitle")}
                        </CardTitle>
                        <div className="flex gap-0.5 rounded-md border p-0.5">
                            {(["sessions", "bytes"] as const).map((m) => (
                                <button
                                    key={m}
                                    className={`rounded px-2 py-0.5 text-xs transition-colors ${
                                        trendMetric === m ? "bg-primary text-primary-foreground" : "text-muted-foreground"
                                    }`}
                                    onClick={() => setTrendMetric(m)}
                                >
                                    {t(m === "bytes" ? "analytics.metricBytes" : "analytics.metricSessions")}
                                </button>
                            ))}
                        </div>
                    </CardHeader>
                    <CardContent>
                        {trend.length === 0 || summary.sessions + summary.sessions_prev === 0 ? (
                            <div className="py-6 text-center text-sm text-muted-foreground">{t("analytics.noData")}</div>
                        ) : (
                            <div className="flex h-28 items-end gap-[2px]" title={t("analytics.trendHint")}>
                                {trend.map((d) => (
                                    <div
                                        key={d.date}
                                        className="group relative h-full flex-1"
                                        title={`${fmtDateShort(d.date)}: ${d.sessions} · ${fmtDuration(d.seconds)} · ${fmtBytes(d.bytes)}`}
                                    >
                                        <div
                                            className="absolute bottom-0 w-full rounded-t bg-primary/70 group-hover:bg-primary"
                                            style={{ height: `${Math.max(2, (trendValue(d) / maxTrend) * 100)}%` }}
                                        />
                                    </div>
                                ))}
                            </div>
                        )}
                        <div className="mt-1 flex justify-between text-xs text-muted-foreground">
                            <span>{trend.length ? fmtDateShort(trend[0].date) : ""}</span>
                            <span>{t(trendMetric === "bytes" ? "analytics.trendUnitBytes" : "analytics.trendUnit")}</span>
                            <span>{trend.length ? fmtDateShort(trend[trend.length - 1].date) : ""}</span>
                        </div>
                    </CardContent>
                </Card>

                {/* 24h 开播分布 */}
                <Card>
                    <CardHeader>
                        <CardTitle className="text-base">{t("analytics.histogramTitle")}</CardTitle>
                    </CardHeader>
                    <CardContent>
                        {summary.sessions === 0 && histogram.every((v) => v === 0) ? (
                            <div className="py-6 text-center text-sm text-muted-foreground">{t("analytics.noData")}</div>
                        ) : (
                            <>
                                <div className="flex h-20 items-end gap-1">
                                    {histogram.map((count, hour) => (
                                        <div
                                            key={hour}
                                            className="group relative h-full flex-1"
                                            title={`${hour}:00–${hour + 1}:00 · ${count}`}
                                        >
                                            <div
                                                className="absolute bottom-0 w-full rounded-t bg-blue-500/60 group-hover:bg-blue-500"
                                                style={{ height: `${Math.max(2, (count / maxHistogram) * 100)}%` }}
                                            />
                                        </div>
                                    ))}
                                </div>
                                <div className="mt-1 flex justify-between text-xs text-muted-foreground">
                                    <span>0</span><span>6</span><span>12</span><span>18</span><span>23</span>
                                </div>
                            </>
                        )}
                        <div className="mt-2 text-xs text-muted-foreground">{t("analytics.cumulativeHint")}</div>
                    </CardContent>
                </Card>

                {/* 主播开播时间分布（按主播，数据累计所有月份） */}
                <Card>
                    <CardHeader>
                        <CardTitle className="text-base">{t("analytics.hoursByStreamerTitle")}</CardTitle>
                    </CardHeader>
                    <CardContent>
                        {streamer_hours.length === 0 ? (
                            <div className="py-6 text-center text-sm text-muted-foreground">{t("analytics.noData")}</div>
                        ) : (
                            <>
                                {/* 时间刻度与柱列对齐（占位与行内名字/角标同宽），上下各一条 */}
                                <div className="flex items-center gap-3 pb-1">
                                    <div className="w-24 shrink-0 sm:w-32" />
                                    <div className="flex min-w-0 flex-1 justify-between text-xs text-muted-foreground">
                                        <span>0</span><span>6</span><span>12</span><span>18</span><span>23</span>
                                    </div>
                                    <div className="w-20 shrink-0" />
                                </div>
                                <div className="max-h-96 space-y-2 overflow-y-auto pr-1">
                                    {streamer_hours.map((s) => (
                                        <StreamerHoursRow
                                            key={s.rec_id}
                                            name={s.name}
                                            hours={s.hours}
                                            total={s.total}
                                            peakHour={s.peak_hour}
                                            peakLabel={t("analytics.peakHour").replace("{h}", String(s.peak_hour))}
                                            peakDesc={t("analytics.peakHourDesc")}
                                        />
                                    ))}
                                </div>
                                <div className="flex items-center gap-3 pt-1">
                                    <div className="w-24 shrink-0 sm:w-32" />
                                    <div className="flex min-w-0 flex-1 justify-between text-xs text-muted-foreground">
                                        <span>0</span><span>6</span><span>12</span><span>18</span><span>23</span>
                                    </div>
                                    <div className="w-20 shrink-0" />
                                </div>
                            </>
                        )}
                        <div className="mt-2 text-xs text-muted-foreground">{t("analytics.cumulativeHint")}</div>
                    </CardContent>
                </Card>

                {/* 排行 */}
                <div className="grid gap-4 lg:grid-cols-3">
                    <Card>
                        <CardHeader>
                            <CardTitle className="text-base">{t("analytics.topSessionsTitle")}</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <BarList
                                emptyText={t("analytics.noData")}
                                rows={rankings.top_sessions.map((r) => ({
                                    label: r.name,
                                    value: r.seconds,
                                    valueText: `${r.sessions} · ${fmtDurationI18n(r.seconds, t)}`,
                                }))}
                            />
                        </CardContent>
                    </Card>
                    <Card>
                        <CardHeader>
                            <CardTitle className="text-base">{t("analytics.topSingleDayTitle")}</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <BarList
                                emptyText={t("analytics.noData")}
                                rows={rankings.top_single_day.map((r) => ({
                                    label: `${r.name} (${fmtDateShort(r.date)})`,
                                    value: r.seconds,
                                    valueText: fmtDurationI18n(r.seconds, t),
                                }))}
                            />
                        </CardContent>
                    </Card>
                    <Card>
                        <CardHeader>
                            <CardTitle className="text-base">{t("analytics.topFrequencyTitle")}</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <BarList
                                emptyText={t("analytics.noData")}
                                rows={rankings.top_frequency.map((r) => ({
                                    label: r.name,
                                    value: r.live_count,
                                    valueText: String(r.live_count),
                                    sub:
                                        r.avg_interval_hours !== null
                                            ? t("analytics.avgInterval").replace("{h}", String(r.avg_interval_hours))
                                            : undefined,
                                }))}
                            />
                        </CardContent>
                    </Card>
                </div>

                {/* 最吃盘 + 检测失败最多 */}
                <div className="grid gap-4 lg:grid-cols-2">
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-1.5 text-base">
                                <Database className="h-4 w-4" />
                                {t("analytics.topBytesTitle")}
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            <BarList
                                emptyText={t("analytics.noData")}
                                rows={rankings.top_bytes.map((r) => ({
                                    label: r.name,
                                    value: r.bytes,
                                    valueText: fmtBytes(r.bytes),
                                    sub:
                                        r.bytes_per_hour !== null
                                            ? t("analytics.bytesPerHour").replace("{size}", fmtBytes(r.bytes_per_hour))
                                            : undefined,
                                }))}
                            />
                        </CardContent>
                    </Card>
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-1.5 text-base">
                                <AlertTriangle className="h-4 w-4" />
                                {t("analytics.topFailuresTitle")}
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            <BarList
                                emptyText={t("analytics.topFailuresEmpty")}
                                rows={rankings.top_failures.map((r) => ({
                                    label: r.name,
                                    value: r.failures,
                                    valueText: `${r.failures}/${r.checks}`,
                                    sub:
                                        r.failure_rate !== null
                                            ? t("analytics.failureRate").replace(
                                                  "{r}",
                                                  (r.failure_rate * 100).toFixed(1),
                                              )
                                            : undefined,
                                }))}
                            />
                        </CardContent>
                    </Card>
                </div>

                {/* 低效清单 + 检测健康度 */}
                <div className="grid gap-4 lg:grid-cols-2">
                    <Card>
                        <CardHeader>
                            <CardTitle className="text-base">{t("analytics.idleTitle")}</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-2">
                            {idle.length === 0 && never_recorded.length === 0 ? (
                                <div className="py-6 text-center text-sm text-muted-foreground">
                                    {t("analytics.idleEmpty")}
                                </div>
                            ) : (
                                <>
                                    {idle.map((r) => (
                                        <div key={r.rec_id} className="flex items-center justify-between gap-2 text-sm">
                                            <span className="min-w-0 truncate">{r.name}</span>
                                            <span className="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
                                                {t("analytics.idleDays").replace("{d}", String(r.idle_days))}
                                                {r.days_left !== null && (
                                                    <Badge variant={r.days_left <= 3 ? "destructive" : "secondary"} className="px-1.5 py-0">
                                                        {t("analytics.daysLeft").replace("{d}", String(r.days_left))}
                                                    </Badge>
                                                )}
                                            </span>
                                        </div>
                                    ))}
                                    {never_recorded.map((r) => (
                                        <div key={r.rec_id} className="flex items-center justify-between gap-2 text-sm">
                                            <span className="min-w-0 truncate">{r.name}</span>
                                            <Badge variant="outline" className="shrink-0 px-1.5 py-0">
                                                {t("analytics.neverRecorded")}
                                            </Badge>
                                        </div>
                                    ))}
                                </>
                            )}
                        </CardContent>
                    </Card>
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-1.5 text-base">
                                <Radio className="h-4 w-4" />
                                {t("analytics.platformTitle")}
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            {platform_checks.length === 0 ? (
                                <div className="py-6 text-center text-sm text-muted-foreground">{t("analytics.noData")}</div>
                            ) : (
                                <div className="space-y-2 text-sm">
                                    {platform_checks.map((p) => (
                                        <div key={p.platform} className="space-y-1">
                                            <div className="flex items-center justify-between gap-2">
                                                <span className="min-w-0 truncate">{p.platform}</span>
                                                <span className="shrink-0 tabular-nums text-muted-foreground">
                                                    {p.checks} · {t("analytics.failureRate").replace("{r}", String((p.failure_rate * 100).toFixed(2)))}
                                                </span>
                                            </div>
                                            {p.failures > 0 && (
                                                <div className="flex flex-wrap gap-1">
                                                    {FAILURE_REASONS.filter((r) => p.reasons[r] > 0).map((r) => (
                                                        <Badge
                                                            key={r}
                                                            variant={r === "invalid" || r === "unsupported" ? "destructive" : "secondary"}
                                                            className="px-1.5 py-0 text-xs font-normal"
                                                        >
                                                            {reasonLabel(r, t)} {p.reasons[r]}
                                                        </Badge>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            )}
                            {failureTotal > 0 && (
                                <div className="mt-3 border-t pt-2 text-xs text-muted-foreground">
                                    <div>
                                        {t("analytics.failureReasonsTitle")}:{" "}
                                        {FAILURE_REASONS.filter((r) => failure_reasons[r] > 0)
                                            .map((r) => `${reasonLabel(r, t)} ${failure_reasons[r]}`)
                                            .join(" · ")}
                                    </div>
                                    <div className="mt-1">{t("analytics.reasonHint")}</div>
                                </div>
                            )}
                        </CardContent>
                    </Card>
                </div>
            </>
        </div>
    )
}
