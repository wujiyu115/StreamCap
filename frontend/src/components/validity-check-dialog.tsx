import { Loader2, Trash2 } from "lucide-react"
import type { ValidityCheckResult } from "@/api/types"
import { Button } from "@/components/ui/button"
import {
    Dialog,
    DialogContent,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table"
import { useI18n } from "@/i18n"

const STATUS_WEIGHT: Record<ValidityCheckResult["status"], number> = {
    invalid: 0,
    error: 1,
    live: 2,
    offline: 3,
}

const STATUS_CLASS: Record<ValidityCheckResult["status"], string> = {
    invalid: "bg-red-500",
    error: "bg-orange-500",
    live: "bg-green-500",
    offline: "bg-amber-500",
}

const STATUS_LABEL_KEY: Record<ValidityCheckResult["status"], string> = {
    invalid: "recordings.validityStatusInvalid",
    error: "recordings.validityStatusError",
    live: "recordings.statusLive",
    offline: "recordings.statusOffline",
}

function ValidityBadge({ result }: { result: ValidityCheckResult }) {
    const { t } = useI18n()
    return (
        <span className="inline-flex items-center gap-1.5">
            <span
                className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium text-white ${
                    STATUS_CLASS[result.status]
                }`}
            >
                {t(STATUS_LABEL_KEY[result.status])}
            </span>
            {result.precise && result.status === "invalid" && (
                <span
                    className="cursor-help rounded-full border border-muted-foreground/40 px-1.5 py-0.5 text-[10px] text-muted-foreground"
                    title={t("recordings.validityPreciseTip")}
                >
                    {t("recordings.validityPreciseBadge")}
                </span>
            )}
        </span>
    )
}

export function ValidityCheckDialog({
    open,
    onOpenChange,
    results,
    checking,
    progress,
    onDeleteInvalid,
    deleting,
    invalidCount,
}: {
    open: boolean
    onOpenChange: (open: boolean) => void
    results: ValidityCheckResult[] | null
    checking: boolean
    progress: { done: number; total: number } | null
    onDeleteInvalid: () => void
    deleting: boolean
    invalidCount: number
}) {
    const { t, tf } = useI18n()

    const counts = (results ?? []).reduce(
        (acc, r) => {
            acc[r.status] += 1
            return acc
        },
        { live: 0, offline: 0, invalid: 0, error: 0 } as Record<ValidityCheckResult["status"], number>,
    )
    const sorted = [...(results ?? [])].sort((a, b) => STATUS_WEIGHT[a.status] - STATUS_WEIGHT[b.status])

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="flex max-h-[85dvh] w-full max-w-3xl flex-col">
                <DialogHeader>
                    <DialogTitle>{t("recordings.validityTitle")}</DialogTitle>
                </DialogHeader>

                {checking && sorted.length === 0 ? (
                    <div className="flex flex-col items-center gap-2 py-16 text-muted-foreground">
                        <Loader2 className="h-6 w-6 animate-spin" />
                        <span className="text-sm">{t("recordings.validityChecking")}</span>
                        {progress && (
                            <span className="text-xs">
                                {tf("recordings.validityProgress", { done: progress.done, total: progress.total })}
                            </span>
                        )}
                    </div>
                ) : sorted.length === 0 ? (
                    <div className="py-16 text-center text-sm text-muted-foreground">
                        {t("recordings.validityEmpty")}
                    </div>
                ) : (
                    <>
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                            {checking && <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />}
                            <span>
                                {checking && progress
                                    ? tf("recordings.validityProgress", { done: progress.done, total: progress.total })
                                    : tf("recordings.validitySummary", {
                                          total: sorted.length,
                                          live: counts.live,
                                          offline: counts.offline,
                                          invalid: counts.invalid,
                                          error: counts.error,
                                      })}
                            </span>
                        </div>
                        <ScrollArea className="min-h-0 flex-1">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>{t("recordings.columnStreamer")}</TableHead>
                                        <TableHead>{t("recordings.columnPlatform")}</TableHead>
                                        <TableHead>{t("recordings.columnUrl")}</TableHead>
                                        <TableHead>{t("recordings.columnStatus")}</TableHead>
                                        <TableHead>{t("recordings.detail")}</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {sorted.map((r) => (
                                        <TableRow key={r.rec_id}>
                                            <TableCell>
                                                <div className="max-w-32 truncate font-medium" title={r.streamer_name}>
                                                    {r.streamer_name || "-"}
                                                </div>
                                                {r.anchor_name && r.anchor_name !== r.streamer_name && (
                                                    <div className="max-w-32 truncate text-xs text-muted-foreground" title={r.anchor_name}>
                                                        {r.anchor_name}
                                                    </div>
                                                )}
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
                                                <ValidityBadge result={r} />
                                            </TableCell>
                                            <TableCell>
                                                {r.detail ? (
                                                    <span className="block max-w-48 truncate text-xs text-muted-foreground" title={r.detail}>
                                                        {r.detail}
                                                    </span>
                                                ) : (
                                                    <span className="text-xs text-muted-foreground">-</span>
                                                )}
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </ScrollArea>
                    </>
                )}

                <DialogFooter className="sm:justify-between">
                    <Button
                        variant="destructive"
                        size="sm"
                        disabled={checking || deleting || invalidCount === 0}
                        onClick={onDeleteInvalid}
                    >
                        {deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
                        <span>{t("recordings.validityDeleteInvalid")}</span>
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
                        {t("common.close")}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}
