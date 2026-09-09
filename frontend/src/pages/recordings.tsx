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
    RotateCcw,
    ScanSearch,
    Square,
    Star,
    Table2,
    Trash2,
} from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { recordingsApi } from "@/api"
import type { Recording, ValidityCheckResult } from "@/api/types"
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
import { displayDuration, formatDuration, formatTimestamp, StatusBadge, stateLabelKey } from "@/components/status"
import { translateError, useI18n } from "@/i18n"
import { toast } from "sonner"
import { CopyIdButton } from "@/components/copy-id-button"
import { RecordingDialog } from "@/components/recording-dialog"
import { ValidityCheckDialog } from "@/components/validity-check-dialog"

type StatusFilter = "all" | "recording" | "live" | "offline" | "error" | "stopped"

const FILTERS: StatusFilter[] = ["all", "recording", "live", "offline", "error", "stopped"]

// 检测分批间隔：两次请求间的停顿，配合后端 limit 分批防风控
const VALIDITY_BATCH_PAUSE_MS = 2000

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
    // 列表切片维度：平台名 / 全部 / 特别关注（占位值 "special"）
    const [platform, setPlatform] = useState<string>("all")
    const [search, setSearch] = useState("")
    const [viewMode, setViewMode] = useState<"table" | "card">("table")
    const [selected, setSelected] = useState<Set<string>>(new Set())
    const [dialogOpen, setDialogOpen] = useState(false)
    const [editing, setEditing] = useState<Recording | null>(null)
    const [validityOpen, setValidityOpen] = useState(false)
    const [validityResults, setValidityResults] = useState<ValidityCheckResult[] | null>(null)
    const [validityPending, setValidityPending] = useState<number | null>(null)
    const [validityProgress, setValidityProgress] = useState<{ done: number; total: number } | null>(null)
    const validityAbortRef = useRef<AbortController | null>(null)

    const { data, isLoading } = useQuery({
        queryKey: ["recordings"],
        queryFn: recordingsApi.list,
        refetchInterval: 5000,
    })

    const recordings = data?.recordings ?? []

    // 打开弹窗时从后端拉检测结果快照（缓存条目 + 待检数，不发检测请求）
    useEffect(() => {
        if (!validityOpen) return
        recordingsApi
            .validitySnapshot()
            .then((d) => {
                if (!validityAbortRef.current) {
                    setValidityResults(d.results)
                    setValidityPending(d.pending)
                }
            })
            .catch(() => undefined)
    }, [validityOpen])

    const platforms = useMemo(
        () => Array.from(new Set(recordings.map((r) => r.platform).filter(Boolean))) as string[],
        [recordings],
    )

    const specialCount = useMemo(
        () => recordings.filter((r) => r.special_attention).length,
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
            if (platform === "special") {
                if (!r.special_attention) return false
            } else if (platform !== "all" && r.platform !== platform) return false
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

    const specialAttentionMutation = useMutation({
        mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
            recordingsApi.update(id, { special_attention: enabled }),
        onSuccess: (_d, vars) =>
            toast.success(
                vars.enabled
                    ? t("recordings.specialAttentionOnTip")
                    : t("recordings.specialAttentionOffTip"),
            ),
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

    const checkValidity = useMutation({
        mutationFn: async (force: boolean): Promise<ValidityCheckResult[]> => {
            const controller = new AbortController()
            validityAbortRef.current = controller
            setValidityProgress({ done: 0, total: 0 })
            // 全量重检：以本次开始时刻为界，早于此的缓存条目全部重检
            const forceSince = force ? Math.floor(Date.now() / 1000) : 0
            let last: ValidityCheckResult[] = []
            for (;;) {
                let d
                try {
                    d = await recordingsApi.checkValidity(
                        { ids: [], force, force_since: forceSince },
                        controller.signal,
                    )
                } catch (e) {
                    if (e instanceof DOMException && e.name === "AbortError") break
                    throw e
                }
                last = d.results
                setValidityResults(d.results)
                setValidityPending(d.pending)
                const total = d.results.length + d.pending
                setValidityProgress({ done: d.results.length, total })
                if (d.pending === 0) break
                await new Promise((r) => setTimeout(r, VALIDITY_BATCH_PAUSE_MS))
            }
            return last
        },
        onSettled: () => {
            setValidityProgress(null)
            validityAbortRef.current = null
        },
        onError: (e: Error) => {
            if (e instanceof DOMException && e.name === "AbortError") return
            toast.error(translateError(e.message))
        },
    })

    const startValidityCheck = (force = false) => {
        if (checkValidity.isPending) return
        checkValidity.mutate(force)
    }

    const stopValidityCheck = () => {
        validityAbortRef.current?.abort()
    }

    const invalidRecIds = useMemo(
        () => (validityResults ?? []).filter((r) => r.status === "invalid").map((r) => r.rec_id),
        [validityResults],
    )

    const handleDeleteInvalid = () => {
        if (invalidRecIds.length === 0) return
        if (confirm(tf("recordings.validityDeleteConfirm", { count: invalidRecIds.length }))) {
            batchDelete.mutate(invalidRecIds, {
                onSuccess: () => {
                    setValidityOpen(false)
                    setValidityResults(null)
                    setValidityPending(null)
                },
            })
        }
    }

    const handleDeleteInvalidOne = (r: ValidityCheckResult) => {
        if (confirm(tf("recordings.deleteOneConfirm", { name: r.streamer_name || r.url }))) {
            deleteMutation.mutate(r.rec_id, {
                onSuccess: () => {
                    setValidityResults((prev) => (prev ?? []).filter((x) => x.rec_id !== r.rec_id))
                    setValidityPending((p) => (p ?? 0) > 0 ? p! - 1 : p)
                },
            })
        }
    }

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

    // 页面满高布局：头部（正在录制+工具栏）固定，仅列表区滚动
    return (
        <div className="flex min-h-0 flex-1 flex-col gap-4">
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
            <div className="flex shrink-0 flex-wrap items-center gap-2">
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

                {/* 重置筛选（状态/平台/搜索）：作用于本排筛选控件，就近收尾 */}
                <Button
                    variant="outline"
                    size="sm"
                    title={t("common.reset")}
                    className="shrink-0"
                    onClick={() => {
                        setFilter("all")
                        setPlatform("all")
                        setSearch("")
                    }}
                >
                    <RotateCcw className="h-4 w-4" />
                </Button>

                <div className="ml-auto flex items-center gap-1.5">
                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <Button variant="outline" size="sm">
                                {platform === "special" && (
                                    <Star className="h-4 w-4 fill-yellow-400 text-yellow-500" />
                                )}
                                <span className="max-w-24 truncate">
                                    {platform === "all"
                                        ? t("common.all")
                                        : platform === "special"
                                          ? t("recordingDialog.specialAttention")
                                          : platform}
                                </span>
                            </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                            <DropdownMenuItem onClick={() => setPlatform("all")}>
                                {t("common.all")}
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => setPlatform("special")}>
                                <Star className="mr-2 h-4 w-4" />
                                {t("recordingDialog.specialAttention")}
                                <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                                    {specialCount}
                                </span>
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
                    <Button
                        variant="outline"
                        size="sm"
                        disabled={recordings.length === 0}
                        onClick={() => setValidityOpen(true)}
                    >
                        <ScanSearch className="h-4 w-4" />
                        <span className="hidden sm:inline">{t("recordings.checkValidity")}</span>
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

            {/* 内容区：独立滚动，头部固定不动 */}
            <div className="min-h-0 flex-1 overflow-y-auto">
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
                                    {t("recordings.columnActivity")}
                                </TableHead>
                                <TableHead className="hidden lg:table-cell">
                                    {t("recordings.columnAdded")}
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
                                        <div className="flex items-center gap-0.5">
                                            <span className="truncate font-medium">{r.streamer_name || "-"}</span>
                                            <CopyIdButton url={r.url} className="h-6 w-6 shrink-0 p-0" iconClassName="h-3 w-3" />
                                            <span className="sm:hidden">
                                                <ActivityBadge rec={r} />
                                            </span>
                                        </div>
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
                                    <TableCell className="hidden sm:table-cell">
                                        <ActivityBadge rec={r} />
                                    </TableCell>
                                    <TableCell className="hidden text-sm text-muted-foreground lg:table-cell">
                                        {r.created_at ? formatTimestamp(r.created_at) : "—"}
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
                                                    r.special_attention
                                                        ? t("recordings.specialAttentionOff")
                                                        : t("recordings.specialAttentionOn")
                                                }
                                                onClick={() =>
                                                    specialAttentionMutation.mutate({
                                                        id: r.rec_id,
                                                        enabled: !r.special_attention,
                                                    })
                                                }
                                            >
                                                <Star
                                                    className={`h-4 w-4 ${
                                                        r.special_attention
                                                            ? "fill-yellow-400 text-yellow-500"
                                                            : "text-muted-foreground"
                                                    }`}
                                                />
                                            </Button>
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
                            onToggleSpecialAttention={() =>
                                specialAttentionMutation.mutate({
                                    id: r.rec_id,
                                    enabled: !r.special_attention,
                                })
                            }
                        />
                    ))}
                </div>
            )}
            </div>

            <RecordingDialog
                open={dialogOpen}
                recording={editing}
                onOpenChange={(open) => {
                    setDialogOpen(open)
                    if (!open) setEditing(null)
                }}
                onSaved={invalidate}
            />

            <ValidityCheckDialog
                open={validityOpen}
                onOpenChange={setValidityOpen}
                results={validityResults}
                checking={checkValidity.isPending}
                progress={validityProgress}
                pending={validityPending}
                onStart={startValidityCheck}
                onStop={stopValidityCheck}
                onDeleteInvalid={handleDeleteInvalid}
                onDeleteOne={handleDeleteInvalidOne}
                deleting={batchDelete.isPending}
                invalidCount={invalidRecIds.length}
            />
        </div>
    )
}

function LiveDuration({ rec }: { rec: Recording }) {
    const seconds = displayDuration(rec)
    return <span>{formatDuration(seconds)}</span>
}

/** 活跃度：口径与监控活跃优先分层一致（上次开播 ≤2 天，或平均开播间隔 ≤3 天 = 活跃） */
function ActivityBadge({ rec }: { rec: Recording }) {
    const { t, tf } = useI18n()
    if (!rec.live_count) {
        return (
            <span
                className="inline-block cursor-help rounded-full border border-muted-foreground/40 px-1.5 py-0.5 text-[10px] text-muted-foreground"
                title={tf("recordings.actTip", { last: "—", n: 0, avg: "—" })}
            >
                {t("recordings.actNever")}
            </span>
        )
    }
    const now = Date.now() / 1000
    const days = rec.last_live_time ? (now - rec.last_live_time) / 86400 : Infinity
    const lastText =
        days < 1 ? `<${t("recordings.actDay")}` : `${Math.floor(days)}${t("recordings.actDay")}`
    const avgText =
        rec.avg_live_interval != null
            ? `${Math.round(rec.avg_live_interval / 3600)}${t("recordings.actHour")}`
            : "—"
    const hot = days <= 2 || (rec.avg_live_interval != null && rec.avg_live_interval <= 3 * 86400)
    return (
        <span
            className={`inline-block cursor-help rounded-full border px-1.5 py-0.5 text-[10px] ${
                hot ? "border-green-500/50 text-green-600" : "border-muted-foreground/40 text-muted-foreground"
            }`}
            title={tf("recordings.actTip", { last: lastText, n: rec.live_count, avg: avgText })}
        >
            {hot ? t("recordings.actHot") : tf("recordings.actCold", { d: Math.floor(days) })}
        </span>
    )
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
    onToggleSpecialAttention,
}: {
    rec: Recording
    selected: boolean
    onToggleSelect: () => void
    onEdit: () => void
    onDelete: () => void
    onMonitor: (enabled: boolean) => void
    onStop: () => void
    onOpenMedia: () => void
    onToggleSpecialAttention: () => void
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
                    <div className="flex items-center gap-0.5">
                        <span className="truncate font-medium">{rec.streamer_name || rec.url}</span>
                        <CopyIdButton url={rec.url} className="h-6 w-6 shrink-0 p-0" iconClassName="h-3 w-3" />
                    </div>
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
                    <ActivityBadge rec={rec} />
                    {rec.is_recording && (
                        <>
                            <span className="font-mono tabular-nums">
                                <LiveDuration rec={rec} />
                            </span>
                            <span className="text-xs">{rec.speed}</span>
                        </>
                    )}
                </div>
                <div className="text-xs">
                    {t("recordings.columnAdded")} {rec.created_at ? formatTimestamp(rec.created_at) : "—"}
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
                <Button
                    variant="outline"
                    size="sm"
                    title={
                        rec.special_attention
                            ? t("recordings.specialAttentionOff")
                            : t("recordings.specialAttentionOn")
                    }
                    onClick={onToggleSpecialAttention}
                >
                    <Star
                        className={`h-3.5 w-3.5 ${
                            rec.special_attention ? "fill-yellow-400 text-yellow-500" : ""
                        }`}
                    />
                </Button>
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
