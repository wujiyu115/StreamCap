import { Copy, Loader2, Play, RotateCw, ScanSearch, ShieldCheck, Square, Trash2 } from "lucide-react"
import type { ValidityCheckResult } from "@/api/types"
import { Button } from "@/components/ui/button"
import {
    Dialog,
    DialogContent,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table"
import { useI18n } from "@/i18n"
import { toast } from "sonner"

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

export function ValidityCheckDialog({
    open,
    onOpenChange,
    results,
    checking,
    progress,
    pending,
    onStart,
    onStop,
    onDeleteInvalid,
    onDeleteOne,
    deleting,
    invalidCount,
}: {
    open: boolean
    onOpenChange: (open: boolean) => void
    results: ValidityCheckResult[] | null
    checking: boolean
    progress: { done: number; total: number } | null
    pending: number | null
    onStart: (force?: boolean) => void
    onStop: () => void
    onDeleteInvalid: () => void
    onDeleteOne: (r: ValidityCheckResult) => void
    deleting: boolean
    invalidCount: number
}) {
    const { t, tf } = useI18n()

    const handleCopy = async (r: ValidityCheckResult) => {
        const text = extractRoomId(r.url)
        if (await copyToClipboard(text)) {
            toast.success(tf("recordings.validityCopied", { text }))
        } else {
            toast.error(t("recordings.validityCopyFailed"))
        }
    }

    const all = results ?? []
    const counts = all.reduce(
        (acc, r) => {
            acc[r.status] += 1
            return acc
        },
        { live: 0, offline: 0, invalid: 0, error: 0 } as Record<ValidityCheckResult["status"], number>,
    )
    const invalidRows = all.filter((r) => r.status === "invalid")
    const pendingCount = pending ?? 0

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="flex max-h-[85dvh] w-full max-w-3xl flex-col overflow-y-hidden">
                <DialogHeader>
                    <DialogTitle>{t("recordings.validityTitle")}</DialogTitle>
                </DialogHeader>

                {checking && progress && progress.total > 0 ? (
                    <div className="flex shrink-0 items-center gap-2 text-sm text-muted-foreground">
                        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
                        <span>
                            {tf("recordings.validityProgress", { done: progress.done, total: progress.total })}
                        </span>
                    </div>
                ) : checking ? (
                    <div className="flex shrink-0 items-center gap-2 text-sm text-muted-foreground">
                        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
                        <span>{t("recordings.validityChecking")}</span>
                    </div>
                ) : all.length > 0 ? (
                    <div className="shrink-0 text-sm text-muted-foreground">
                        {tf("recordings.validitySummary", {
                            total: all.length,
                            live: counts.live,
                            offline: counts.offline,
                            invalid: counts.invalid,
                            error: counts.error,
                        })}
                        {pendingCount > 0 && (
                            <span className="ml-1 text-orange-500">
                                {tf("recordings.validityPending", { count: pendingCount })}
                            </span>
                        )}
                    </div>
                ) : null}

                {checking && invalidRows.length === 0 ? (
                    <div className="flex flex-1 flex-col items-center gap-2 py-16 text-muted-foreground">
                        <Loader2 className="h-6 w-6 animate-spin" />
                        <span className="text-sm">{t("recordings.validityChecking")}</span>
                    </div>
                ) : invalidRows.length > 0 ? (
                    <div className="min-h-0 flex-1 overflow-y-auto">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>{t("recordings.columnStreamer")}</TableHead>
                                    <TableHead>{t("recordings.columnPlatform")}</TableHead>
                                    <TableHead>{t("recordings.columnUrl")}</TableHead>
                                    <TableHead>{t("recordings.columnStatus")}</TableHead>
                                    <TableHead>{t("recordings.detail")}</TableHead>
                                    <TableHead className="w-20">{t("common.operations")}</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {invalidRows.map((r) => (
                                    <TableRow key={r.rec_id}>
                                        <TableCell>
                                            <div className="max-w-32 truncate font-medium" title={r.streamer_name}>
                                                {r.streamer_name || "-"}
                                            </div>
                                        </TableCell>
                                        <TableCell className="whitespace-nowrap text-muted-foreground">
                                            {r.platform || "-"}
                                        </TableCell>
                                        <TableCell>
                                            <a
                                                href={r.url}
                                                target="_blank"
                                                rel="noreferrer"
                                                className="block max-w-48 truncate text-primary hover:underline"
                                                title={r.url}
                                            >
                                                {r.url}
                                            </a>
                                        </TableCell>
                                        <TableCell>
                                            <span className="inline-flex items-center gap-1.5">
                                                <span className="inline-flex items-center rounded-full bg-red-500 px-2.5 py-0.5 text-xs font-medium text-white">
                                                    {t("recordings.validityStatusInvalid")}
                                                </span>
                                                {r.precise && (
                                                    <span
                                                        className="cursor-help rounded-full border border-muted-foreground/40 px-1.5 py-0.5 text-[10px] text-muted-foreground"
                                                        title={t("recordings.validityPreciseTip")}
                                                    >
                                                        {t("recordings.validityPreciseBadge")}
                                                    </span>
                                                )}
                                            </span>
                                        </TableCell>
                                        <TableCell>
                                            {r.detail ? (
                                                <span
                                                    className="block max-w-48 truncate text-xs text-muted-foreground"
                                                    title={r.detail}
                                                >
                                                    {r.detail}
                                                </span>
                                            ) : (
                                                <span className="text-xs text-muted-foreground">-</span>
                                            )}
                                        </TableCell>
                                        <TableCell>
                                            <div className="flex items-center gap-1">
                                                <Button
                                                    variant="outline"
                                                    size="sm"
                                                    className="h-7 w-7 p-0"
                                                    title={t("recordings.validityCopy")}
                                                    onClick={() => handleCopy(r)}
                                                >
                                                    <Copy className="h-3.5 w-3.5" />
                                                </Button>
                                                <Button
                                                    variant="outline"
                                                    size="sm"
                                                    className="h-7 w-7 p-0"
                                                    title={t("recordings.validityDeleteOne")}
                                                    onClick={() => onDeleteOne(r)}
                                                >
                                                    <Trash2 className="h-3.5 w-3.5 text-red-500" />
                                                </Button>
                                            </div>
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </div>
                ) : results && pendingCount === 0 ? (
                    <div className="flex flex-1 flex-col items-center gap-2 py-16 text-muted-foreground">
                        <ShieldCheck className="h-8 w-8 text-green-500" />
                        <span className="text-sm">{t("recordings.validityNoInvalid")}</span>
                    </div>
                ) : (
                    <div className="flex flex-1 flex-col items-center gap-2 py-16 text-muted-foreground">
                        <ScanSearch className="h-8 w-8 text-muted-foreground/50" />
                        <span className="text-sm">{t("recordings.validityIdleHint")}</span>
                    </div>
                )}

                <DialogFooter className="shrink-0 gap-2 sm:justify-between">
                    <Button
                        variant="destructive"
                        size="sm"
                        disabled={checking || deleting || invalidCount === 0}
                        onClick={onDeleteInvalid}
                    >
                        {deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                        <span>{t("recordings.validityDeleteInvalid")}</span>
                    </Button>
                    {checking ? (
                        <Button variant="outline" size="sm" onClick={onStop}>
                            <Square className="h-4 w-4" />
                            <span>{t("recordings.validityStop")}</span>
                        </Button>
                    ) : (
                        <div className="flex items-center gap-2">
                            <Button size="sm" onClick={() => onStart(false)}>
                                <Play className="h-4 w-4" />
                                <span>{t("recordings.validityStart")}</span>
                            </Button>
                            <Button variant="outline" size="sm" onClick={() => onStart(true)}>
                                <RotateCw className="h-4 w-4" />
                                <span>{t("recordings.validityRecheck")}</span>
                            </Button>
                        </div>
                    )}
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}
