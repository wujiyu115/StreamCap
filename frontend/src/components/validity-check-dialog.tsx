import { Loader2, ShieldCheck, Trash2 } from "lucide-react"
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

    const all = results ?? []
    const counts = all.reduce(
        (acc, r) => {
            acc[r.status] += 1
            return acc
        },
        { live: 0, offline: 0, invalid: 0, error: 0 } as Record<ValidityCheckResult["status"], number>,
    )
    const invalidRows = all.filter((r) => r.status === "invalid")

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="flex max-h-[85dvh] w-full max-w-3xl flex-col overflow-y-hidden">
                <DialogHeader>
                    <DialogTitle>{t("recordings.validityTitle")}</DialogTitle>
                </DialogHeader>

                {checking && progress ? (
                    <div className="flex shrink-0 items-center gap-2 text-sm text-muted-foreground">
                        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
                        <span>
                            {tf("recordings.validityProgress", { done: progress.done, total: progress.total })}
                        </span>
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
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </div>
                ) : results ? (
                    <div className="flex flex-1 flex-col items-center gap-2 py-16 text-muted-foreground">
                        <ShieldCheck className="h-8 w-8 text-green-500" />
                        <span className="text-sm">{t("recordings.validityNoInvalid")}</span>
                    </div>
                ) : (
                    <div className="py-16 text-center text-sm text-muted-foreground">
                        {t("recordings.validityEmpty")}
                    </div>
                )}

                <DialogFooter className="shrink-0 sm:justify-between">
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
