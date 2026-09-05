import { useQuery } from "@tanstack/react-query"
import { CalendarDays, Clock, Film, Loader2, Radio, TrendingDown, TrendingUp, Users } from "lucide-react"
import { useState } from "react"
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

function fmtBytes(bytes: number): string {
    if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
    if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${bytes} B`
}

export default function AnalyticsPage() {
    const { t } = useI18n()
    const [days, setDays] = useState(30)
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

    const { summary, trend, rankings, idle, never_recorded, histogram, platform_checks, storage } = data
    const maxTrendSessions = Math.max(...trend.map((d) => d.sessions), 1)
    const maxHistogram = Math.max(...histogram, 1)
    const changePct = summary.sessions_change_pct

    return (
        <div className="flex min-h-0 flex-1 flex-col gap-4">
            {/* 标题 + 时间范围 */}
            <div className="flex shrink-0 flex-wrap items-center gap-2">
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
            <div className="grid shrink-0 grid-cols-2 gap-3 lg:grid-cols-4">
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
                />
                <StatCard
                    icon={<CalendarDays className="h-5 w-5" />}
                    label={t("analytics.filesLabel")}
                    value={String(summary.files)}
                />
                <StatCard
                    icon={<Users className="h-5 w-5" />}
                    label={t("analytics.activeAnchorsLabel")}
                    value={`${summary.active_anchors}/${summary.monitoring}`}
                />
            </div>

            <div className="list-scroll min-h-0 flex-1 space-y-4 overflow-y-auto pr-0.5">
                {/* 趋势：每日场次 */}
                <Card>
                    <CardHeader>
                        <CardTitle className="text-base">{t("analytics.trendTitle")}</CardTitle>
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
                                        title={`${fmtDateShort(d.date)}: ${d.sessions} · ${fmtDuration(d.seconds)}`}
                                    >
                                        <div
                                            className="absolute bottom-0 w-full rounded-t bg-primary/70 group-hover:bg-primary"
                                            style={{ height: `${Math.max(2, (d.sessions / maxTrendSessions) * 100)}%` }}
                                        />
                                    </div>
                                ))}
                            </div>
                        )}
                        <div className="mt-1 flex justify-between text-xs text-muted-foreground">
                            <span>{trend.length ? fmtDateShort(trend[0].date) : ""}</span>
                            <span>{t("analytics.trendUnit")}</span>
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
                                        <div key={p.platform} className="flex items-center justify-between gap-2">
                                            <span className="min-w-0 truncate">{p.platform}</span>
                                            <span className="shrink-0 tabular-nums text-muted-foreground">
                                                {p.checks} · {t("analytics.failureRate").replace("{r}", String((p.failure_rate * 100).toFixed(2)))}
                                            </span>
                                        </div>
                                    ))}
                                </div>
                            )}
                            {storage.files.length > 0 && (
                                <div
                                    className="mt-3 border-t pt-2 text-xs text-muted-foreground"
                                    title={storage.files.map((f) => `${f.name}: ${fmtBytes(f.bytes)}`).join("\n")}
                                >
                                    {t("analytics.storageSize")
                                        .replace("{size}", fmtBytes(storage.total_bytes))
                                        .replace("{n}", String(storage.files.length))}
                                </div>
                            )}
                        </CardContent>
                    </Card>
                </div>
            </div>
        </div>
    )
}
