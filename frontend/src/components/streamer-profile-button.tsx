import { ExternalLink, Loader2 } from "lucide-react"
import { useState } from "react"
import { recordingsApi } from "@/api"
import { Button } from "@/components/ui/button"
import { translateError, useI18n } from "@/i18n"
import { toast } from "sonner"

/**
 * 「打开主播主页」小图标：抖音/B 站的主页地址要后端查一次（房间号 ≠ 主播号），
 * 其它平台房间页就是主页。先同步开一个空白标签页再导航，避免 await 之后被
 * 浏览器当成非用户手势弹窗拦掉。
 */
export function StreamerProfileButton({
    recId,
    className,
    iconClassName = "h-3.5 w-3.5",
}: {
    recId: string
    className?: string
    iconClassName?: string
}) {
    const { t } = useI18n()
    const [loading, setLoading] = useState(false)

    const open = async (e: React.MouseEvent) => {
        e.stopPropagation()
        if (loading) return
        const tab = window.open("about:blank", "_blank")
        if (tab) {
            try {
                tab.document.title = t("recordings.openingProfile")
                tab.document.body.textContent = t("recordings.openingProfile")
            } catch {
                /* 拿不到引用也没关系，导航照样能走 */
            }
        }
        setLoading(true)
        try {
            const res = await recordingsApi.profileUrl(recId)
            if (tab) {
                tab.location.href = res.url
            } else {
                // 空白页被拦了，退化成普通新标签（此时仍是同一个点击手势链上）
                window.open(res.url, "_blank", "noopener,noreferrer")
            }
            if (res.kind !== "profile") {
                toast.info(t("recordings.profileFallback"))
            }
        } catch (err) {
            tab?.close()
            const msg = err instanceof Error ? translateError(err.message) : String(err)
            toast.error(msg || t("common.failed"))
        } finally {
            setLoading(false)
        }
    }

    return (
        <Button
            variant="ghost"
            size="sm"
            className={className}
            title={t("recordings.openProfile")}
            aria-label={t("recordings.openProfile")}
            data-testid={`open-profile-${recId}`}
            disabled={loading}
            onClick={open}
        >
            {loading ? <Loader2 className={`animate-spin ${iconClassName}`} /> : <ExternalLink className={iconClassName} />}
        </Button>
    )
}
