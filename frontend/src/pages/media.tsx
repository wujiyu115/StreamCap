import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
    ChevronRight,
    FileVideo,
    Folder,
    FolderOpen,
    Loader2,
    Play,
    Sparkles,
    Trash2,
} from "lucide-react"
import { useEffect, useState } from "react"
import { mediaApi, poseApi } from "@/api"
import type { MediaItem } from "@/api/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { formatTimestamp } from "@/components/status"
import { PlayerDialog } from "@/components/player-dialog"
import { PoseTaskPanel } from "@/components/pose-task-panel"
import { useVideoMeta } from "@/hooks/use-video-meta"
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

export default function MediaPage() {
    const { t, tf } = useI18n()
    const queryClient = useQueryClient()
    const [path, setPath] = useState("")
    const [selected, setSelected] = useState<Set<string>>(new Set())
    const [previewItem, setPreviewItem] = useState<MediaItem | null>(null)
    const [cleanOpen, setCleanOpen] = useState(false)
    const [cleanMb, setCleanMb] = useState("10")

    const { data: tree, isLoading } = useQuery({
        queryKey: ["media-tree", path],
        queryFn: () => mediaApi.tree(path),
    })
    const { data: stats } = useQuery({
        queryKey: ["media-stats", path],
        queryFn: () => mediaApi.stats(path),
    })

    const invalidate = () => {
        queryClient.invalidateQueries({ queryKey: ["media-tree"] })
        queryClient.invalidateQueries({ queryKey: ["media-stats"] })
    }

    const deleteMutation = useMutation({
        mutationFn: (rel: string) => mediaApi.remove(rel),
        onSuccess: () => {
            toast.success(t("common.success"))
            invalidate()
        },
        onError: (e: Error) => toast.error(e.message),
    })

    const batchDeleteMutation = useMutation({
        mutationFn: (paths: string[]) => mediaApi.batchDelete(paths),
        onSuccess: (d) => {
            toast.success(tf("media.deleteSuccess", { count: d.deleted }))
            if (d.failed.length) toast.warning(tf("media.deleteFailedItems", { count: d.failed.length }))
            setSelected(new Set())
            invalidate()
        },
        onError: (e: Error) => toast.error(e.message),
    })

    const cleanMutation = useMutation({
        mutationFn: () => mediaApi.clean(path, Math.round(parseFloat(cleanMb || "0") * 1024 * 1024)),
        onSuccess: (d) => {
            toast.success(
                tf("media.cleanResult", {
                    files: d.deleted_files,
                    dirs: d.deleted_dirs,
                    skipped: d.skipped_products,
                }),
            )
            setCleanOpen(false)
            invalidate()
        },
        onError: (e: Error) => toast.error(e.message),
    })

    const items = tree?.items ?? []
    const protectedFiles = new Set((stats?.protected_files ?? []).map((p) => p))
    const segments = path ? path.split("/") : []

    // 预览队列：当前目录的媒体文件按显示顺序，供播放器上一个/下一个切换
    const previewQueue = items.filter((i) => i.type !== "folder")

    const poseMutation = useMutation({
        mutationFn: (paths: string[]) => poseApi.submit(paths),
        onSuccess: () => {
            toast.success(t("pose.submitted"))
            queryClient.invalidateQueries({ queryKey: ["pose-tasks"] })
        },
        onError: (e: Error) => toast.error(e.message),
    })

    const submitPose = () => {
        const videos = Array.from(selected).filter((rel) => {
            const item = items.find((i) => i.rel_path === rel)
            return item && item.type === "video"
        })
        if (videos.length === 0) return
        if (confirm(tf("pose.submitConfirm", { count: videos.length }))) {
            poseMutation.mutate(videos)
        }
    }

    const navigate = (rel: string) => {
        setPath(rel)
        setSelected(new Set())
    }

    const toggleSelect = (rel: string) => {
        setSelected((prev) => {
            const next = new Set(prev)
            if (next.has(rel)) next.delete(rel)
            else next.add(rel)
            return next
        })
    }

    const selectableFiles = items.filter((i) => i.type !== "folder")
    const allSelected =
        selectableFiles.length > 0 && selectableFiles.every((f) => selected.has(f.rel_path))

    const handleDelete = (item: MediaItem) => {
        if (confirm(tf("media.deleteFileConfirm", { name: item.name }))) {
            deleteMutation.mutate(item.rel_path)
        }
    }

    const handleBatchDelete = () => {
        if (selected.size === 0) return
        if (confirm(tf("media.deleteBatchConfirm", { count: selected.size }))) {
            batchDeleteMutation.mutate(Array.from(selected))
        }
    }

    return (
        <div className="space-y-4">
            {/* 统计与工具栏 */}
            <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-xl font-bold">{t("media.title")}</h1>
                {stats && (
                    <Badge variant="secondary">
                        {tf("media.stats", { files: stats.video_files, size: stats.total_size })}
                    </Badge>
                )}
                <div className="ml-auto flex gap-1.5">
                    <Button
                        variant="outline"
                        size="sm"
                        disabled={selected.size === 0 || poseMutation.isPending}
                        onClick={submitPose}
                    >
                        <Sparkles className="h-4 w-4 text-purple-500" />
                        <span className="hidden sm:inline">{t("media.poseSubmit")}</span>
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => setCleanOpen(true)}>
                        <Trash2 className="h-4 w-4" />
                        <span className="hidden sm:inline">{t("media.cleanSmall")}</span>
                    </Button>
                    <Button
                        variant="destructive"
                        size="sm"
                        disabled={selected.size === 0}
                        onClick={handleBatchDelete}
                    >
                        <Trash2 className="h-4 w-4" />
                        <span className="hidden sm:inline">
                            {t("media.batchDelete")} ({selected.size})
                        </span>
                        <span className="sm:hidden">{selected.size}</span>
                    </Button>
                </div>
            </div>

            {/* 面包屑 */}
            <div className="flex items-center gap-1 overflow-x-auto text-sm">
                <button
                    className="flex shrink-0 items-center gap-1 text-muted-foreground hover:text-foreground"
                    onClick={() => navigate("")}
                >
                    <FolderOpen className="h-4 w-4" />
                    {t("media.breadcrumbRoot")}
                </button>
                {segments.map((seg, i) => (
                    <span key={i} className="flex shrink-0 items-center gap-1">
                        <ChevronRight className="h-3 w-3 text-muted-foreground" />
                        <button
                            className={`whitespace-nowrap ${
                                i === segments.length - 1
                                    ? "font-medium"
                                    : "text-muted-foreground hover:text-foreground"
                            }`}
                            onClick={() => navigate(segments.slice(0, i + 1).join("/"))}
                        >
                            {seg}
                        </button>
                    </span>
                ))}
            </div>

            {/* 文件表 */}
            {isLoading ? (
                <div className="flex justify-center py-20">
                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
            ) : items.length === 0 ? (
                <div className="rounded-lg border border-dashed py-20 text-center text-muted-foreground">
                    {t("media.empty")}
                </div>
            ) : (
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
                                                    ? new Set(selectableFiles.map((f) => f.rel_path))
                                                    : new Set(),
                                            )
                                        }
                                    />
                                </TableHead>
                                <TableHead>{t("media.columnName")}</TableHead>
                                <TableHead className="hidden w-20 sm:table-cell">{t("media.columnDuration")}</TableHead>
                                <TableHead className="hidden w-24 sm:table-cell">{t("media.columnSize")}</TableHead>
                                <TableHead className="hidden w-40 md:table-cell">{t("media.columnModified")}</TableHead>
                                <TableHead className="w-24 text-right">{t("common.operations")}</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {path && (
                                <TableRow className="cursor-pointer" onClick={() => navigate(segments.slice(0, -1).join("/"))}>
                                    <TableCell />
                                    <TableCell colSpan={4} className="text-sm text-muted-foreground">
                                        <span className="flex items-center gap-2">
                                            <FolderOpen className="h-4 w-4" /> {t("media.up")}
                                        </span>
                                    </TableCell>
                                </TableRow>
                            )}
                            {items.map((item) => (
                                <TableRow
                                    key={item.rel_path}
                                    className={
                                        item.type === "folder" ? "cursor-pointer" : undefined
                                    }
                                    onClick={() => item.type === "folder" && navigate(item.rel_path)}
                                    data-state={
                                        previewItem?.rel_path === item.rel_path
                                            ? "previewing"
                                            : selected.has(item.rel_path)
                                              ? "selected"
                                              : undefined
                                    }
                                >
                                    <TableCell onClick={(e) => e.stopPropagation()}>
                                        {item.type !== "folder" && (
                                            <Checkbox
                                                checked={selected.has(item.rel_path)}
                                                onCheckedChange={() => toggleSelect(item.rel_path)}
                                            />
                                        )}
                                    </TableCell>
                                    <TableCell>
                                        <span className="flex items-center gap-2">
                                            {item.type === "folder" ? (
                                                <Folder className="h-4 w-4 text-blue-500" />
                                            ) : (
                                                <FileVideo className="h-4 w-4 text-muted-foreground" />
                                            )}
                                            <span className="font-medium">{item.name}</span>
                                            {item.type === "folder" && (
                                                <span className="text-xs text-muted-foreground">
                                                    {item.count} {t("media.items")}
                                                </span>
                                            )}
                                        </span>
                                    </TableCell>
                                    <TableCell className="hidden font-mono text-xs text-muted-foreground tabular-nums sm:table-cell">
                                        <DurationCell item={item} />
                                    </TableCell>
                                    <TableCell className="hidden text-sm text-muted-foreground sm:table-cell">
                                        {item.size ?? "-"}
                                    </TableCell>
                                    <TableCell className="hidden text-sm text-muted-foreground md:table-cell">
                                        {formatTimestamp(item.mtime)}
                                    </TableCell>
                                    <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
                                        {item.type !== "folder" && (
                                            <div className="flex justify-end gap-1">
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    title={t("media.preview")}
                                                    onClick={() => setPreviewItem(item)}
                                                >
                                                    <Play className="h-4 w-4" />
                                                </Button>
                                                <Button
                                                    variant="ghost"
                                                    size="icon"
                                                    title={t("common.delete")}
                                                    onClick={() => handleDelete(item)}
                                                >
                                                    <Trash2 className="h-4 w-4 text-red-500" />
                                                </Button>
                                            </div>
                                        )}
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            )}

            {/* 清理对话框 */}
            <Dialog open={cleanOpen} onOpenChange={setCleanOpen}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>{t("media.cleanSmall")}</DialogTitle>
                    </DialogHeader>
                    <div className="space-y-3">
                        <div className="space-y-1.5">
                            {t("media.cleanThreshold")}
                            <Input
                                type="number"
                                value={cleanMb}
                                onChange={(e) => setCleanMb(e.target.value)}
                            />
                        </div>
                        <p className="text-xs text-muted-foreground">{t("media.cleanThresholdDesc")}</p>
                        <Button
                            className="w-full"
                            disabled={cleanMutation.isPending}
                            onClick={() => cleanMutation.mutate()}
                        >
                            {cleanMutation.isPending && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
                            {t("common.confirm")}
                        </Button>
                    </div>
                </DialogContent>
            </Dialog>

            {/* 预览弹窗：工具栏（上一个/下一个/删除/关闭） */}
            {previewItem && (
                <PlayerDialog
                    item={previewItem}
                    streamUrl={mediaApi.streamUrl}
                    hasPrev={previewQueue.indexOf(previewItem) > 0}
                    hasNext={previewQueue.indexOf(previewItem) < previewQueue.length - 1}
                    onPrev={() => {
                        const idx = previewQueue.indexOf(previewItem)
                        if (idx > 0) setPreviewItem(previewQueue[idx - 1])
                    }}
                    onNext={() => {
                        const idx = previewQueue.indexOf(previewItem)
                        if (idx < previewQueue.length - 1) setPreviewItem(previewQueue[idx + 1])
                    }}
                    onClose={() => setPreviewItem(null)}
                    onDelete={(rel) => {
                        if (confirm(tf("media.deleteFileConfirm", { name: rel.split("/").pop() ?? rel }))) {
                            deleteMutation.mutate(rel, {
                                onSettled: () => {
                                    const idx = previewQueue.findIndex((i) => i.rel_path === rel)
                                    const next =
                                        previewQueue[idx + 1] ?? previewQueue[idx - 1] ?? null
                                    setPreviewItem(next)
                                },
                            })
                        }
                    }}
                />
            )}

            {/* 人体识别任务进度 */}
            <PoseTaskPanel />
        </div>
    )
}

function DurationCell({ item }: { item: MediaItem }) {
    const meta = useVideoMeta(item.type === "video" ? item.rel_path : null, item.type)
    if (item.type !== "video") return <span className="text-muted-foreground/40">—</span>
    if (!meta) return <span className="text-muted-foreground/40">…</span>
    return (
        <span title={meta.resolution}>
            {meta.duration}
            {meta.resolution ? (
                <span className="ml-1 hidden text-[10px] text-muted-foreground/60 xl:inline">
                    {meta.resolution}
                </span>
            ) : null}
        </span>
    )
}
