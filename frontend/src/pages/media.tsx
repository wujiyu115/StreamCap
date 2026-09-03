import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
    ArrowLeft,
    ChevronRight,
    FileVideo,
    Folder,
    FolderOpen,
    LayoutGrid,
    List,
    Loader2,
    Play,
    RefreshCw,
    Sparkles,
    Trash2,
} from "lucide-react"
import { useEffect, useMemo, useRef, useState } from "react"
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
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { formatTimestamp } from "@/components/status"
import { PlayerDialog } from "@/components/player-dialog"
import { PoseTaskPanel } from "@/components/pose-task-panel"
import { useVideoMeta } from "@/hooks/use-video-meta"
import { useI18n } from "@/i18n"
import { toast } from "sonner"
import { useSearchParams } from "react-router-dom"

export default function MediaPage() {
    const { t, tf } = useI18n()
    const queryClient = useQueryClient()
    // 录制管理「打开目录」跳转带 ?path=；导航内切换时同步更新 URL
    const [searchParams, setSearchParams] = useSearchParams()
    const [path, setPathState] = useState(() => searchParams.get("path") ?? "")

    const setPath = (next: string) => {
        setPathState(next)
        setSearchParams(next ? { path: next } : {}, { replace: true })
    }
    const [selected, setSelected] = useState<Set<string>>(new Set())
    const [previewItem, setPreviewItem] = useState<MediaItem | null>(null)
    const [lastViewed, setLastViewed] = useState<string | null>(null)
    const [cleanOpen, setCleanOpen] = useState(false)
    const [cleanMb, setCleanMb] = useState("10")
    const [search, setSearch] = useState("")
    const [viewMode, setViewMode] = useState<"list" | "grid">(
        () => (localStorage.getItem("media-view") as "list" | "grid") || "list",
    )

    // 各目录滚动位置记忆（Frostcast 同款）：离开时存，回来时恢复。
    // 实际滚动容器是 AppLayout 的 <main>
    const scrollPos = useRef<Record<string, number>>({})
    const pendingRestore = useRef<number | null>(null)
    const getScroller = () => document.querySelector("main") as HTMLElement | null

    const { data: tree, isLoading } = useQuery({
        queryKey: ["media-tree", path],
        queryFn: () => mediaApi.tree(path),
    })
    const { data: stats } = useQuery({
        queryKey: ["media-stats", path],
        queryFn: () => mediaApi.stats(path),
    })

    useEffect(() => {
        if (pendingRestore.current != null) {
            const scroller = getScroller()
            if (scroller) scroller.scrollTop = pendingRestore.current
            pendingRestore.current = null
        }
    }, [tree])

    useEffect(() => {
        localStorage.setItem("media-view", viewMode)
    }, [viewMode])

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

    const allItems = tree?.items ?? []
    const items = useMemo(() => {
        const q = search.trim().toLowerCase()
        return q ? allItems.filter((it) => it.name.toLowerCase().includes(q)) : allItems
    }, [allItems, search])
    const protectedFiles = new Set((stats?.protected_files ?? []).map((p) => p))
    const segments = path ? path.split("/") : []

    // 预览队列：当前目录的媒体文件按显示顺序，供播放器上一个/下一个切换
    const previewQueue = useMemo(() => items.filter((i) => i.type !== "folder"), [items])

    const poseMutation = useMutation({
        mutationFn: (paths: string[]) => poseApi.submit(paths),
        onSuccess: () => {
            toast.success(t("pose.submitted"))
            queryClient.invalidateQueries({ queryKey: ["pose-tasks"] })
        },
        onError: (e: Error) => {
            if (e.message.includes("still being written")) {
                const m = e.message.match(/: (.+)$/)
                toast.error(tf("pose.notReadyError", { files: m?.[1] ?? "" }))
            } else {
                toast.error(e.message)
            }
        },
    })

    const submitPose = () => {
        // 文件夹也允许提交（后端递归展开为目录内全部视频）
        const targets = Array.from(selected).filter((rel) => {
            const item = items.find((i) => i.rel_path === rel)
            return item && (item.type === "video" || item.type === "folder")
        })
        if (targets.length === 0) return
        if (confirm(tf("pose.submitConfirm", { count: targets.length }))) {
            poseMutation.mutate(targets)
        }
    }

    const navigate = (rel: string) => {
        // 离开前记录当前目录滚动位置（返回时恢复）
        const scroller = getScroller()
        if (scroller) scrollPos.current[path] = scroller.scrollTop
        pendingRestore.current = scrollPos.current[rel] ?? 0
        setPath(rel)
        setSelected(new Set())
        setSearch("")
        setLastViewed(null)
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
        selectableFiles.length > 0 && items.every((i) => i.type !== "folder" || selected.has(i.rel_path))

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
                <div className="ml-auto flex items-center gap-1.5">
                    <Input
                        placeholder={t("media.searchDir")}
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="hidden w-44 md:block"
                    />
                    <div className="flex gap-0.5 rounded-md border p-0.5">
                        <button
                            className={`rounded px-2 py-1 ${viewMode === "list" ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}
                            title={t("media.listView")}
                            onClick={() => setViewMode("list")}
                        >
                            <List className="h-4 w-4" />
                        </button>
                        <button
                            className={`rounded px-2 py-1 ${viewMode === "grid" ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}
                            title={t("media.gridView")}
                            onClick={() => setViewMode("grid")}
                        >
                            <LayoutGrid className="h-4 w-4" />
                        </button>
                    </div>
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

            {/* 面包屑：返回上级 + 路径 + 刷新 + 子目录跳转 */}
            <div className="flex items-center gap-1 overflow-x-auto text-sm">
                <button
                    className="flex shrink-0 items-center gap-1 rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-40"
                    title={t("media.up")}
                    disabled={!path}
                    onClick={() => navigate(segments.slice(0, -1).join("/"))}
                >
                    <ArrowLeft className="h-4 w-4" />
                </button>
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
                <button
                    className="ml-1 flex shrink-0 items-center rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
                    title={t("common.refresh")}
                    onClick={() => invalidate()}
                >
                    <RefreshCw className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`} />
                </button>
                <SubdirJump
                    folders={allItems
                        .filter((i) => i.type === "folder")
                        .map((i) => ({ name: i.name, rel_path: i.rel_path }))}
                    onGoto={navigate}
                />
            </div>

            {/* 文件区 */}
            {isLoading ? (
                <div className="flex justify-center py-20">
                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
            ) : items.length === 0 ? (
                <div className="rounded-lg border border-dashed py-20 text-center text-muted-foreground">
                    {search ? t("media.noResults") : t("media.empty")}
                </div>
            ) : (
                <div className={`media-grid${viewMode === "list" ? " list-mode" : ""}`}>
                    {items.map((item, i) => (
                        <MediaCard
                            key={item.rel_path}
                            item={item}
                            index={i}
                            selected={selected.has(item.rel_path)}
                            lastViewed={lastViewed === item.rel_path}
                            previewing={previewItem?.rel_path === item.rel_path}
                            onOpen={(it) => {
                                setPreviewItem(it)
                                setLastViewed(it.rel_path)
                            }}
                            onEnter={navigate}
                            onDelete={handleDelete}
                            onToggleSelect={toggleSelect}
                        />
                    ))}
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


/** 大图标卡片（Frostcast 风格）：16:10 缩略图区 + 居中播放钮 + 时长角标 + 元信息 tags */
function MediaCard({
    item,
    index,
    selected,
    lastViewed,
    previewing,
    onOpen,
    onEnter,
    onDelete,
    onToggleSelect,
}: {
    item: MediaItem
    index: number
    selected: boolean
    lastViewed: boolean
    previewing: boolean
    onOpen: (item: MediaItem) => void
    onEnter: (rel: string) => void
    onDelete: (item: MediaItem) => void
    onToggleSelect: (rel: string) => void
}) {
    const { t } = useI18n()
    const meta = useVideoMeta(item.type === "video" ? item.rel_path : null, item.type)

    const onClick = () => {
        if (item.type === "folder") onEnter(item.rel_path)
        else onOpen(item)
    }

    const cls = [
        "media-card",
        selected ? "selected" : "",
        lastViewed && !previewing ? "last-viewed" : "",
        previewing ? "selected" : "",
    ]
        .filter(Boolean)
        .join(" ")

    return (
        <article className={cls} onClick={onClick}>
            <div
                className={`media-thumb ${
                    item.type === "folder" ? "folder-thumb" : item.type === "image" ? "" : `g${index % 6}`
                }`}
            >
                <label
                    className="absolute left-2 top-2 z-10 grid h-6 w-6 place-items-center rounded-md bg-black/40"
                    onClick={(e) => e.stopPropagation()}
                    title={t("media.select")}
                >
                    <Checkbox
                        className="h-4 w-4"
                        checked={selected}
                        onCheckedChange={() => onToggleSelect(item.rel_path)}
                    />
                </label>
                {item.type === "folder" ? (
                    <>
                        <Folder className="h-14 w-14 fill-primary/20 text-primary/80" />
                        <span className="count-badge">
                            {item.count ?? 0} {t("media.items")}
                        </span>
                    </>
                ) : item.type === "image" ? (
                    <>
                        <img src={mediaApi.streamUrl(item.rel_path)} alt={item.name} loading="lazy" />
                        <span className="dur-badge">{t("media.image")}</span>
                    </>
                ) : (
                    <>
                        <span className="play-btn">
                            {/* 三角形视觉重心偏左，右移 3px 补偿（原版同款） */}
                            <Play className="ml-[3px] h-5 w-5 fill-white" />
                        </span>
                        <span className="dur-badge">{meta?.duration ?? "…"}</span>
                    </>
                )}
            </div>
            <div className="meta-row">
                <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium" title={item.name}>
                        {item.name}
                    </div>
                </div>
                <div className="flex w-full flex-wrap items-center gap-1.5">
                    {item.type !== "folder" && <span className="tag">{item.ext}</span>}
                    {meta?.resolution && <span className="tag">{meta.resolution}</span>}
                    {item.size && <span className="tag">{item.size}</span>}
                    <span className="tag">{formatTimestamp(item.mtime)}</span>
                    {item.type !== "folder" && (
                        <button
                            className="ml-auto rounded p-1 text-muted-foreground hover:bg-accent hover:text-red-500"
                            title={t("common.delete")}
                            onClick={(e) => {
                                e.stopPropagation()
                                onDelete(item)
                            }}
                        >
                            <Trash2 className="h-3.5 w-3.5" />
                        </button>
                    )}
                </div>
            </div>
        </article>
    )
}

/** 子目录快速跳转下拉（Popover 走 Portal 渲染，避免被面包屑 overflow-x-auto 裁剪） */
function SubdirJump({
    folders,
    onGoto,
}: {
    folders: Array<{ name: string; rel_path: string }>
    onGoto: (rel: string) => void
}) {
    const { t } = useI18n()
    const [open, setOpen] = useState(false)

    if (folders.length === 0) return null

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
                <button
                    type="button"
                    className="flex shrink-0 items-center gap-1 rounded px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
                >
                    {t("media.subdirs")} ({folders.length})
                    <ChevronRight
                        className={`h-3 w-3 transition-transform ${open ? "rotate-90" : "-rotate-90"}`}
                    />
                </button>
            </PopoverTrigger>
            <PopoverContent align="start" className="max-h-64 w-56 overflow-y-auto p-1">
                {folders.map((f) => (
                    <button
                        key={f.rel_path}
                        type="button"
                        className="flex w-full items-center gap-2 truncate rounded px-2 py-1.5 text-left text-sm hover:bg-accent"
                        onClick={() => {
                            setOpen(false)
                            onGoto(f.rel_path)
                        }}
                    >
                        <Folder className="h-4 w-4 shrink-0 text-blue-500" />
                        <span className="truncate">{f.name}</span>
                    </button>
                ))}
            </PopoverContent>
        </Popover>
    )
}
