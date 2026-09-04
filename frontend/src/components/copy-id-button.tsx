import { Copy } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { useI18n } from "@/i18n"

function extractRoomId(url: string): string {
    try {
        const parts = new URL(url).pathname.split("/").filter(Boolean)
        return parts.length ? parts[parts.length - 1] : url
    } catch {
        return url
    }
}

async function copyToClipboard(text: string): Promise<boolean> {
    // 只在安全上下文（https/localhost）用异步 clipboard API；
    // http 下该 API 不可用或会被拒，且 await 失败后再降级会丢失用户手势导致 execCommand 也失败
    if (window.isSecureContext && navigator.clipboard?.writeText) {
        try {
            await navigator.clipboard.writeText(text)
            return true
        } catch {
            /* 降级 */
        }
    }
    const ta = document.createElement("textarea")
    ta.value = text
    ta.setAttribute("readonly", "")
    ta.style.position = "fixed"
    ta.style.opacity = "0"
    document.body.appendChild(ta)
    if (/ipad|iphone|ipod/i.test(navigator.userAgent)) {
        // iOS Safari 的 textarea.select() 不生效，需 Range + setSelectionRange 组合
        const range = document.createRange()
        range.selectNodeContents(ta)
        const sel = window.getSelection()
        sel?.removeAllRanges()
        sel?.addRange(range)
        ta.setSelectionRange(0, text.length)
    } else {
        ta.select()
    }
    let ok = false
    try {
        ok = document.execCommand("copy")
    } catch {
        ok = false
    }
    document.body.removeChild(ta)
    return ok
}

/** 复制主播ID（URL 里的房间号）小图标按钮 */
export function CopyIdButton({
    url,
    variant = "ghost",
    className,
    iconClassName = "h-3.5 w-3.5",
}: {
    url: string
    variant?: "ghost" | "outline"
    className?: string
    iconClassName?: string
}) {
    const { t, tf } = useI18n()

    const handleCopy = async () => {
        const text = extractRoomId(url)
        if (await copyToClipboard(text)) {
            toast.success(tf("recordings.validityCopied", { text }))
        } else {
            toast.error(t("recordings.validityCopyFailed"))
        }
    }

    return (
        <Button
            variant={variant}
            size="sm"
            className={className}
            title={t("recordings.validityCopy")}
            onClick={(e) => {
                e.stopPropagation()
                handleCopy()
            }}
        >
            <Copy className={iconClassName} />
        </Button>
    )
}
