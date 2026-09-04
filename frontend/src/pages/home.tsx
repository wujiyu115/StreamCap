import { useQuery } from "@tanstack/react-query"
import { Radio, Settings, Video } from "lucide-react"
import { Link } from "react-router-dom"
import { systemApi } from "@/api"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useI18n } from "@/i18n"

export default function HomePage() {
    const { t, tf } = useI18n()
    const { data: stats } = useQuery({ queryKey: ["system-stats"], queryFn: systemApi.stats, refetchInterval: 10_000 })
    const { data: info } = useQuery({ queryKey: ["system-info"], queryFn: systemApi.info, staleTime: 300_000 })

    const cards = [
        { label: t("home.totalRecordings"), value: stats?.total_recordings ?? "-", filter: "all" },
        { label: t("home.activeRecordings"), value: stats?.active_recordings ?? "-", filter: "recording" },
        { label: t("home.monitoring"), value: stats?.monitoring_recordings ?? "-", filter: "live" },
        { label: t("home.stoppedMonitoring"), value: stats?.stopped_monitoring ?? "-", filter: "stopped" },
    ]

    const lang = document.documentElement.lang || "zh_CN"

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-2xl font-bold">{t("home.greeting")}</h1>
                {info?.version && (
                    <p className="text-sm text-muted-foreground">
                        {t("home.version")}: {info.version}
                    </p>
                )}
            </div>

            <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                {cards.map((c) => (
                    <Link key={c.label} to={`/recordings?filter=${c.filter}`} className="group">
                        <Card className="transition-colors group-hover:border-primary/50">
                            <CardContent className="p-4">
                                <div className="text-2xl font-bold">{c.value}</div>
                                <div className="text-sm text-muted-foreground">{c.label}</div>
                            </CardContent>
                        </Card>
                    </Link>
                ))}
                <Card>
                    <CardContent className="p-4">
                        <div className="text-2xl font-bold">{stats?.storage?.total_size ?? "-"}</div>
                        <div className="text-sm text-muted-foreground">
                            {t("home.storageUsage")} · {stats?.storage?.video_files ?? 0} {t("home.files")}
                        </div>
                    </CardContent>
                </Card>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle className="text-base">{t("home.quickActions")}</CardTitle>
                </CardHeader>
                <CardContent className="flex gap-3">
                    <Link
                        to="/recordings"
                        className="flex items-center gap-2 rounded-md border px-4 py-2 text-sm hover:bg-accent"
                    >
                        <Radio className="h-4 w-4" /> {t("home.goRecordings")}
                    </Link>
                    <Link
                        to="/media"
                        className="flex items-center gap-2 rounded-md border px-4 py-2 text-sm hover:bg-accent"
                    >
                        <Video className="h-4 w-4" /> {t("home.goMedia")}
                    </Link>
                    <Link
                        to="/settings"
                        className="flex items-center gap-2 rounded-md border px-4 py-2 text-sm hover:bg-accent"
                    >
                        <Settings className="h-4 w-4" /> {t("home.goSettings")}
                    </Link>
                </CardContent>
            </Card>

            {info?.announcement?.[lang]?.length ? (
                <Card>
                    <CardHeader>
                        <CardTitle className="text-base">{t("home.announcement")}</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        {info.announcement[lang].map((a, i) => (
                            <div key={i}>
                                <div className="font-medium">{a.title}</div>
                                <div className="text-sm text-muted-foreground">{a.content}</div>
                            </div>
                        ))}
                    </CardContent>
                </Card>
            ) : null}
        </div>
    )
}
