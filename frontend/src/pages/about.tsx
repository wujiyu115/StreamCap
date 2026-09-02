import { useMutation, useQuery } from "@tanstack/react-query"
import { RefreshCw } from "lucide-react"
import { systemApi } from "@/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useI18n } from "@/i18n"
import { toast } from "sonner"

export default function AboutPage() {
    const { t, tf } = useI18n()
    const { data: info } = useQuery({ queryKey: ["system-info"], queryFn: systemApi.info, staleTime: 300_000 })

    const checkUpdate = useMutation({
        mutationFn: systemApi.checkUpdate,
        onSuccess: (result) => {
            const data = result as { has_update?: boolean; latest_version?: string; error?: string }
            if (data.has_update) {
                toast.success(tf("about.hasUpdate", { version: data.latest_version ?? "?" }))
            } else if (data.error) {
                toast.error(`${t("about.updateFailed")}: ${data.error}`)
            } else {
                toast.success(t("about.noUpdate"))
            }
        },
        onError: () => toast.error(t("about.updateFailed")),
    })

    const lang = (localStorage.getItem("streamcap.lang") as "zh_CN" | "en") ?? "zh_CN"

    return (
        <div className="mx-auto max-w-3xl space-y-4">
            <h1 className="text-2xl font-bold">{t("about.title")}</h1>

            <Card>
                <CardHeader>
                    <CardTitle className="text-base">StreamCap</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                    <div className="flex justify-between">
                        <span className="text-muted-foreground">{t("about.version")}</span>
                        <span className="font-mono">{info?.version ?? "-"}</span>
                    </div>
                    <div className="flex justify-between">
                        <span className="text-muted-foreground">{t("about.kernelVersion")}</span>
                        <span className="font-mono">{info?.kernel_version ?? "-"}</span>
                    </div>
                    <div className="flex justify-between">
                        <span className="text-muted-foreground">{t("about.releaseDate")}</span>
                        <span className="font-mono">{info?.release_date ?? "-"}</span>
                    </div>
                    <div className="flex justify-between">
                        <span className="text-muted-foreground">{t("about.license")}</span>
                        <span>{info?.open_source_license ?? "-"}</span>
                    </div>
                    <div className="pt-2">
                        <Button
                            variant="outline"
                            size="sm"
                            disabled={checkUpdate.isPending}
                            onClick={() => checkUpdate.mutate()}
                        >
                            <RefreshCw className={`h-4 w-4 ${checkUpdate.isPending ? "animate-spin" : ""}`} />
                            {checkUpdate.isPending ? t("about.checkingUpdate") : t("about.checkUpdate")}
                        </Button>
                    </div>
                </CardContent>
            </Card>

            {info?.introduction?.[lang] && (
                <Card>
                    <CardHeader>
                        <CardTitle className="text-base">{t("about.title")}</CardTitle>
                    </CardHeader>
                    <CardContent className="text-sm text-muted-foreground">
                        {info.introduction[lang]}
                    </CardContent>
                </Card>
            )}

            {info?.updates?.[lang]?.length ? (
                <Card>
                    <CardHeader>
                        <CardTitle className="text-base">{t("about.changelog")}</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <ul className="list-inside list-disc space-y-1 text-sm text-muted-foreground">
                            {info.updates[lang].map((u, i) => (
                                <li key={i}>{u}</li>
                            ))}
                        </ul>
                    </CardContent>
                </Card>
            ) : null}
        </div>
    )
}
