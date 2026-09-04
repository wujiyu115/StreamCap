import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
    Eye,
    EyeOff,
    FolderOpen,
    LayoutGrid,
    Loader2,
    MoreVertical,
    Pencil,
    Play,
    Plus,
    RefreshCw,
    Square,
    Table2,
    Trash2,
} from "lucide-react"
import { useMemo, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { recordingsApi } from "@/api"
import type { Recording } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table"
import { displayDuration, formatDuration, StatusBadge, stateLabelKey } from "@/components/status"
import { useI18n } from "@/i18n"
import { toast } from "sonner"
import { RecordingDialog } from "@/components/recording-dialog"

type StatusFilter = "all" | "recording" | "live" | "offline" | "error" | "stopped"

const FILTERS: StatusFilter[] = ["all", "recording", "live", "offline", "error", "stopped"]
const FILTER_LABEL_KEY: Record<StatusFilter, string> = {
    all: "recordings.statusAll",
    recording: "recordings.statusRecording",
    live: "recordings.statusLive",
    offline: "recordings.statusOffline",
    error: "recordings.statusError",
    stopped: "recordings.statusStopped",
}

export default function RecordingsPage() {
    const { t, tf } = useI18n()
    const navigate = useNavigate()
    const queryClient = useQueryClient()
    const [searchParams] = useSearchParams()
    // 主页状态卡片跳转带 ?filter=xxx 预选状态 tab
    const [filter, setFilter] = useState<StatusFilter>(() => {
        const f = searchParams.get("filter")
        return FILTERS.includes(f as StatusFilter) ? (f as StatusFilter) : "all"
    })
    const [platform, setPlatform] = useState<string>("all")
    const [search, setSearch] = useState("")
    const [viewMode, setViewMode] = useState<"table" | "card">("table")
    const [selected, setSelected] = useState<Set<string>>(new Set())
    const [dialogOpen, setDialogOpen] = useState(false)
    const [editing, setEditing] = useState<Recording | null>(null)

    const { data, isLoading, refetch } = useQuery({
        queryKey: ["recordings"],
        queryFn: recordingsApi.list,
        refetchInterval: 5000,
    })

    const recordings = data?.recordings ?? []

    const platforms = useMemo(
        () => Array.from(new Set(recordings.map((r) => r.platform).filter(Boolean))) as string[],
        [recordings],
    )

    // 各状态总数（只按状态维度统计，不受平台/搜索筛选影响）
    const stateCounts = useMemo(() => {
        const counts: Record<StatusFilter, number> = {
            all: recordings.length,
            recording: 0,
            live: 0,
            offline: 0,
            error: 0,
            stopped: 0,
        }
        for (const r of recordings) {
            if (r.is_recording || r.state === "live") counts.recording += 1
            if (r.state === "live") counts.live += 1
            else if (r.state === "offline") counts.offline += 1
            else if (r.state === "error") counts.error += 1
            else if (r.state === "stopped") counts.stopped += 1
        }
        return counts
    }, [recordings])

    const filtered = useMemo(() => {
        return recordings.filter((r) => {
            if (filter === "recording" && !(r.is_recording || r.state === "live")) return false
            if (filter === "live" && r.state !== "live") return false
            if (filter === "offline" && r.state !== "offline") return false
            if (filter === "error" && r.state !== "error") return false
            if (filter === "stopped" && r.state !== "stopped") return false
            if (platform !== "all" && r.platform !== platform) return false
            if (search) {
                const q = search.toLowerCase()
                if (
                    !r.streamer_name?.toLowerCase().includes(q) &&
                    !r.url.toLowerCase().includes(q) &&
                    !r.platform?.toLowerCase().includes(q)
                ) {
                    return false
                }
            }
            return true
        })
    }, [recordings, filter, platform, search])

    const nowRecording = recordings.filter((r) => r.is_recording)

    const invalidate = () => queryClient.invalidateQueries({ queryKey: ["recordings"] })

    const monitorMutation = useMutation({
        mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
            recordingsApi.setMonitor(id, enabled),
        onSuccess: (_d, vars) =>
            toast.success(vars.enabled ? t("recordings.startMonitorTip") : t("recordings.stopMonitorTip")),
        onSettled: invalidate,
    })

    const stopMutation = useMutation({
        mutationFn: recordingsApi.stop,
        onSuccess: () => toast.success(t("recordings.stopRecordingTip")),
        onSettled: invalidate,
    })

    const deleteMutation = useMutation({
        mutationFn: recordingsApi.remove,
        onSuccess: () => toast.success(t("recordings.deleteSuccess")),
        onSettled: invalidate,
    })

    const batchMonitor = useMutation({
        mutationFn: ({ ids, enabled }: { ids: string[]; enabled: boolean }) =>
            recordingsApi.batchMonitor(ids, enabled),
        onSettled: () => {
            setSelected(new Set())
            invalidate()
        },
    })

    const batchDelete = useMutation({
        mutationFn: (ids: string[]) => recordingsApi.batchDelete(ids),
        onSuccess: () => toast.success(t("recordings.deleteSuccess")),
        onSettled: () => {
            setSelected(new Set())
            invalidate()
        },
    })

    const toggleSelect = (id: string) => {
        setSelected((prev) => {
            const next = new Set(prev)
            if (next.has(id)) next.delete(id)
            else next.add(id)
            return next
        })
    }

    const allSelected = filtered.length > 0 && filtered.every((r) => selected.has(r.rec_id))

    const handleDelete = (rec: Recording) => {
        if (confirm(tf("recordings.deleteOneConfirm", { name: rec.streamer_name || rec.url }))) {
            deleteMutation.mutate(rec.rec_id)
        }
    }

    // media_path: 任务录制目录相对媒体根的路径；null=从未录制过（跳媒体根）, ""=根目录本身
    const gotoMedia = (rec: Recording) => {
        const p = rec.media_path ?? ""
        navigate(p ? `/media?path=${encodeURIComponent(p)}` : "/media")
    }

    const handleBatchDelete = () => {
        if (selected.size === 0) return
        if (confirm(tf("recordings.deleteConfirm", { count: selected.size }))) {
            batchDelete.mutate(Array.from(selected))
        }
    }

    return (
        <div className="space-y-4">
            {/* 正在录制实时面板 */}
            <div className="rounded-lg border bg-card p-3">
                <div className="mb-2 text-sm font-medium text-muted-foreground">
                    {t("recordings.nowRecording")}
                </div>
                {nowRecording.length === 0 ? (
                    <div className="text-sm text-muted-foreground">{t("recordings.noRecording")}</div>
                ) : (
                    <div className="flex flex-wrap gap-2">
                        {nowRecording.map((r) => (
                            <div
                                key={r.rec_id}
                                className="flex items-center gap-2 rounded-full border bg-green-500/10 px-3 py-1 text-sm"
                            >
                                <span className="h-2 w-2 animate-pulse rounded-full bg-green-500" />
                                <span className="font-medium">{r.streamer_name}</span>
                                <span className="text-muted-foreground">{t(`quality.${r.quality}`)}</span>
                                <span className="font-mono tabular-nums">
                                    <LiveDuration rec={r} />
                                </span>
                                {r.speed && r.speed !== "X KB/s" && (
                                    <span className="text-xs text-muted-foreground">{r.speed}</span>
                                )}
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* 工具栏 */}
            <div className="flex flex-wrap items-center gap-2">
                <div className="flex gap-1 overflow-x-auto rounded-md border p-0.5">
                    {FILTERS.map((f) => (
                        <button
                            key={f}
                            onClick={() => setFilter(f)}
                            className={`flex items-center gap-1.5 whitespace-nowrap rounded px-2.5 py-1 text-sm transition-colors ${
                                filter === f
                                    ? "bg-primary text-primary-foreground"
                                    : "text-muted-foreground hover:bg-accent"
                            }`}
                        >
                            {t(FILTER_LABEL_KEY[f])}
                            <span
                                className={`rounded-full px-1.5 text-xs tabular-nums ${
                                    filter === f
                                        ? "bg-primary-foreground/20"
                                        : "bg-muted text-muted-foreground"
                                }`}
                            >
                                {stateCounts[f]}
                            </span>
                        </button>
                    ))}
                </div>

                <Input
                    placeholder={t("common.search")}
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    className="w-full sm:w-56"
                />

                <div className="ml-auto flex items-center gap-1.5">
                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <Button variant="outline" size="sm">
                                <span className="max-w-24 truncate">
                                    {platform === "all" ? t("common.all") : platform}
                                </span>
                            </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                            <DropdownMenuItem onClick={() => setPlatform("all")}>
                                {t("common.all")}
                            </DropdownMenuItem>
                            {platforms.map((p) => (
                                <DropdownMenuItem key={p} onClick={() => setPlatform(p)}>
                                    {p}
                                </DropdownMenuItem>
                            ))}
                        </DropdownMenuContent>
                    </DropdownMenu>
                    <Button
                        size="sm"
                        onClick={() => {
                            setEditing(null)
                            setDialogOpen(true)
                        }}
                    >
                        <Plus className="h-4 w-4" />
                        <span className="hidden sm:inline">{t("recordings.add")}</span>
                    </Button>
                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <Button variant="outline" size="sm" disabled={selected.size === 0}>
                                <MoreVertical className="h-4 w-4" />
                                {selected.size > 0 && (
                                    <span className="ml-1 rounded-full bg-primary px-1.5 text-xs text-primary-foreground">
                                        {selected.size}
                                    </span>
                                )}
                            </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                            <DropdownMenuItem
                                onClick={() =>
                                    batchMonitor.mutate({ ids: Array.from(selected), enabled: true })
                                }
                            >
                                <Eye className="mr-2 h-4 w-4" />
                                {t("recordings.batchStart")}
                            </DropdownMenuItem>
                            <DropdownMenuItem
                                onClick={() =>
                                    batchMonitor.mutate({ ids: Array.from(selected), enabled: false })
                                }
                            >
                                <EyeOff className="mr-2 h-4 w-4" />
                                {t("recordings.batchStop")}
                            </DropdownMenuItem>
                            <DropdownMenuItem
                                className="text-red-600"
                                onClick={handleBatchDelete}
                            >
                                <Trash2 className="mr-2 h-4 w-4" />
                                {t("recordings.batchDelete")}
                            </DropdownMenuItem>
                        </DropdownMenuContent>
                    </DropdownMenu>
                    <Button variant="outline" size="sm" onClick={() => refetch()}>
                        <RefreshCw className="h-4 w-4" />
                    </Button>
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setViewMode((m) => (m === "table" ? "card" : "table"))}
                    >
                        {viewMode === "table" ? (
                            <LayoutGrid className="h-4 w-4" />
                        ) : (
                            <Table2 className="h-4 w-4" />
                        )}
                    </Button>
                </div>
            </div>

            {/* 内容区 */}
            {isLoading ? (
                <div className="flex justify-center py-20">
                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
            ) : filtered.length === 0 ? (
                <div className="rounded-lg border border-dashed py-20 text-center text-muted-foreground">
                    {recordings.length === 0 ? t("recordings.empty") : t("recordings.noResults")}
                </div>
            ) : viewMode === "table" ? (
                <div className="rounded-lg border bg-card">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead className="w-10">
                                    <Checkbox
                                        checked={allSelected}
                                        onCheckedChange={(v) =>
                                            setSelected(
                                                v
                                                    ? new Set(filtered.map((r) => r.rec_id))
                                                    : new Set(),
                                            )
                                        }
                                    />
                                </TableHead>
                                <TableHead>{t("recordings.columnStreamer")}</TableHead>
                                <TableHead className="hidden md:table-cell">
                                    {t("recordings.columnPlatform")}
                                </TableHead>
                                <TableHead>{t("recordings.columnStatus")}</TableHead>
                                <TableHead className="hidden md:table-cell">
                                    {t("recordings.columnQuality")}
                                </TableHead>
                                <TableHead className="hidden sm:table-cell">
                                    {t("recordings.columnDuration")}
                                </TableHead>
                                <TableHead className="text-right">{t("common.operations")}</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {filtered.map((r) => (
                                <TableRow key={r.rec_id} data-state={selected.has(r.rec_id) ? "selected" : undefined}>
                                    <TableCell>
                                        <Checkbox
                                            checked={selected.has(r.rec_id)}
                                            onCheckedChange={() => toggleSelect(r.rec_id)}
                                        />
                                    </TableCell>
                                    <TableCell>
                                        <div className="font-medium">{r.streamer_name || "-"}</div>
                                        <div className="max-w-48 truncate text-xs text-muted-foreground sm:max-w-64">
                                            {r.live_title || r.url}
                                        </div>
                                    </TableCell>
                                    <TableCell className="hidden text-sm text-muted-foreground md:table-cell">
                                        {r.platform || "-"}
                                    </TableCell>
                                    <TableCell>
                                        <div className="flex items-center gap-1.5">
                                            <StatusBadge state={r.state} label={t(stateLabelKey(r.state))} />
                                            {r.unsupported && (
                                                <span
                                                    className="cursor-help rounded-full border border-muted-foreground/40 px-1.5 py-0.5 text-[10px] text-muted-foreground"
                                                    title={t("recordings.unsupportedTip")}
                                                >
                                                    {t("recordings.statusUnsupported")}
                                                </span>
                                            )}
                                        </div>
                                    </TableCell>
                                    <TableCell className="hidden md:table-cell">
                                        {t(`quality.${r.quality}`)}
                                    </TableCell>
                                    <TableCell className="hidden font-mono text-sm tabular-nums sm:table-cell">
                                        <LiveDuration rec={r} />
                                    </TableCell>
                                    <TableCell className="text-right">
                                        <div className="flex justify-end gap-1">
                                            {r.is_recording ? (
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    title={t("recordings.stopRecord")}
                                                    onClick={() => stopMutation.mutate(r.rec_id)}
                                                >
                                                    <Square className="h-4 w-4 text-red-500" />
                                                </Button>
                                            ) : (
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    title={t("recordings.startRecord")}
                                                    disabled={!r.monitor_status}
                                                    onClick={() => monitorMutation.mutate({ id: r.rec_id, enabled: true })}
                                                >
                                                    <Play className="h-4 w-4 text-green-600" />
                                                </Button>
                                            )}
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                title={
                                                    r.monitor_status
                                                        ? t("recordings.stopMonitor")
                                                        : t("recordings.startMonitor")
                                                }
                                                onClick={() =>
                                                    monitorMutation.mutate({
                                                        id: r.rec_id,
                                                        enabled: !r.monitor_status,
                                                    })
                                                }
                                            >
                                                {r.monitor_status ? (
                                                    <Eye className="h-4 w-4" />
                                                ) : (
                                                    <EyeOff className="h-4 w-4 text-muted-foreground" />
                                                )}
                                            </Button>
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                title={t("recordings.openMediaDir")}
                                                onClick={() => gotoMedia(r)}
                                            >
                                                <FolderOpen className="h-4 w-4" />
                                            </Button>
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                title={t("common.edit")}
                                                onClick={() => {
                                                    setEditing(r)
                                                    setDialogOpen(true)
                                                }}
                                            >
                                                <Pencil className="h-4 w-4" />
                                            </Button>
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                title={t("common.delete")}
                                                onClick={() => handleDelete(r)}
                                            >
                                                <Trash2 className="h-4 w-4 text-red-500" />
                                            </Button>
                                        </div>
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            ) : (
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                    {filtered.map((r) => (
                        <RecordingCardView
                            key={r.rec_id}
                            rec={r}
                            selected={selected.has(r.rec_id)}
                            onToggleSelect={() => toggleSelect(r.rec_id)}
                            onEdit={() => {
                                setEditing(r)
                                setDialogOpen(true)
                            }}
                            onDelete={() => handleDelete(r)}
                            onMonitor={(enabled) => monitorMutation.mutate({ id: r.rec_id, enabled })}
                            onStop={() => stopMutation.mutate(r.rec_id)}
                            onOpenMedia={() => gotoMedia(r)}
                        />
                    ))}
                </div>
            )}

            <RecordingDialog
                open={dialogOpen}
                recording={editing}
                onOpenChange={(open) => {
                    setDialogOpen(open)
                    if (!open) setEditing(null)
                }}
                onSaved={invalidate}
            />
        </div>
    )
}

function LiveDuration({ rec }: { rec: Recording }) {
    const seconds = displayDuration(rec)
    return <span>{formatDuration(seconds)}</span>
}

function RecordingCardView({
    rec,
    selected,
    onToggleSelect,
    onEdit,
    onDelete,
    onMonitor,
    onStop,
    onOpenMedia,
}: {
    rec: Recording
    selected: boolean
    onToggleSelect: () => void
    onEdit: () => void
    onDelete: () => void
    onMonitor: (enabled: boolean) => void
    onStop: () => void
    onOpenMedia: () => void
}) {
    const { t } = useI18n()
    return (
        <div
            className={`cursor-pointer rounded-lg border bg-card p-4 transition-shadow hover:shadow-md ${
                selected ? "ring-2 ring-primary" : ""
            }`}
            onClick={onToggleSelect}
        >
            <div className="mb-2 flex items-start justify-between gap-2">
                <div className="min-w-0">
                    <div className="truncate font-medium">{rec.streamer_name || rec.url}</div>
                    <div className="text-xs text-muted-foreground">{rec.platform}</div>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                    {rec.unsupported && (
                        <span
                            className="cursor-help rounded-full border border-muted-foreground/40 px-1.5 py-0.5 text-[10px] text-muted-foreground"
                            title={t("recordings.unsupportedTip")}
                        >
                            {t("recordings.statusUnsupported")}
                        </span>
                    )}
                    <StatusBadge state={rec.state} label={t(stateLabelKey(rec.state))} />
                </div>
            </div>
            <div className="mb-3 space-y-1 text-sm text-muted-foreground">
                <div className="truncate text-xs">{rec.live_title || rec.url}</div>
                <div className="flex items-center gap-3">
                    <span>{t(`quality.${rec.quality}`)}</span>
                    {rec.is_recording && (
                        <>
                            <span className="font-mono tabular-nums">
                                <LiveDuration rec={rec} />
                            </span>
                            <span className="text-xs">{rec.speed}</span>
                        </>
                    )}
                </div>
            </div>
            <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                {rec.is_recording ? (
                    <Button variant="outline" size="sm" onClick={onStop}>
                        <Square className="h-3.5 w-3.5" />
                    </Button>
                ) : (
                    <Button
                        variant="outline"
                        size="sm"
                        disabled={!rec.monitor_status}
                        onClick={() => onMonitor(true)}
                    >
                        <Play className="h-3.5 w-3.5" />
                    </Button>
                )}
                <Button variant="outline" size="sm" onClick={() => onMonitor(!rec.monitor_status)}>
                    {rec.monitor_status ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
                </Button>
                <Button variant="outline" size="sm" title={t("recordings.openMediaDir")} onClick={onOpenMedia}>
                    <FolderOpen className="h-3.5 w-3.5" />
                </Button>
                <Button variant="outline" size="sm" onClick={onEdit}>
                    <Pencil className="h-3.5 w-3.5" />
                </Button>
                <Button variant="outline" size="sm" onClick={onDelete}>
                    <Trash2 className="h-3.5 w-3.5 text-red-500" />
                </Button>
            </div>
        </div>
    )
}
