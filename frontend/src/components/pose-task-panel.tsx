import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Loader2, Sparkles, Square, X } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { poseApi } from "@/api"
import type { PoseTaskState } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import { useI18n } from "@/i18n"
import { toast } from "sonner"

const STATUS_KEY: Record<string, string> = {
    running: "pose.statusRunning",
    completed: "pose.statusCompleted",
    failed: "pose.statusFailed",
    cancelled: "pose.statusStopped",
}

const STATE_KEY: Record<string, string> = {
    waiting: "pose.statusWaiting",
    loading_model: "pose.statusRunning",
    processing: "pose.statusRunning",
    starting: "pose.statusRunning",
}

/** 人体识别任务：右侧悬浮按钮 + 点开悬浮进度面板。

 * 有任务时显示悬浮按钮（运行中带环形进度与旋转图标），点击展开面板
 * 看详情/日志/停止；无任务时整组隐藏，不占页面空间。
 */
export function PoseTaskPanel() {
    const { t, tf } = useI18n()
    const queryClient = useQueryClient()
    const [open, setOpen] = useState(false)
    const [logOpen, setLogOpen] = useState(false)
    const [logText, setLogText] = useState("")
    const rootRef = useRef<HTMLDivElement | null>(null)

    // 点击外部收起
    useEffect(() => {
        if (!open) return
        const onDown = (e: MouseEvent | TouchEvent) => {
            if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
        }
        document.addEventListener("mousedown", onDown)
        document.addEventListener("touchstart", onDown)
        return () => {
            document.removeEventListener("mousedown", onDown)
            document.removeEventListener("touchstart", onDown)
        }
    }, [open])

    const { data } = useQuery({
        queryKey: ["pose-tasks"],
        queryFn: poseApi.list,
        refetchInterval: (query) => {
            const running = query.state.data?.tasks?.some((task) => task.status === "running")
            return running ? 1500 : 10_000
        },
    })

    const tasks = data?.tasks ?? []
    const running = tasks.find((task) => task.status === "running")
    const latest = running ?? tasks[0]

    useEffect(() => {
        if (!logOpen || !latest?.task_id) return
        let offset = 0
        let cancelled = false
        const timer = setInterval(async () => {
            try {
                const result = await poseApi.log(latest.task_id!, offset)
                if (cancelled) return
                if (result.chunk) setLogText((prev) => prev + result.chunk)
                offset = result.next_offset
            } catch {
                /* task may have finished */
            }
        }, 1000)
        return () => {
            cancelled = true
            clearInterval(timer)
        }
    }, [logOpen, latest?.task_id])

    const stopMutation = useMutation({
        mutationFn: () => poseApi.stop(latest!.task_id!),
        onSuccess: () => toast.success(t("pose.stopped")),
        onError: (e: Error) => toast.error(e.message),
        onSettled: () => queryClient.invalidateQueries({ queryKey: ["pose-tasks"] }),
    })

    if (!latest) {
        return null
    }

    const statusLabel = t(STATE_KEY[latest.state ?? ""] ?? STATUS_KEY[latest.status] ?? "pose.statusRunning")
    const percent = latest.total_percent ?? 0

    return (
        <div ref={rootRef} className="pointer-events-none fixed inset-0 z-40">
            {/* 悬浮按钮：始终在右缘垂直居中 */}
            <button
                type="button"
                className={`pointer-events-auto fixed right-3 top-1/2 z-40 grid h-12 w-12 -translate-y-1/2 place-items-center rounded-full border shadow-lg transition-colors md:right-5 ${
                    latest.status === "running"
                        ? "border-purple-400/60 bg-purple-600 text-white"
                        : "border-border bg-card text-foreground"
                }`}
                title={t("pose.taskProgress")}
                onClick={(e) => {
                    e.stopPropagation()
                    setOpen((v) => !v)
                }}
            >
                {latest.status === "running" ? (
                    <Loader2 className="h-6 w-6 animate-spin" />
                ) : (
                    <Sparkles className="h-6 w-6 text-purple-500" />
                )}
                {latest.status === "running" && percent > 0 && (
                    <span className="absolute -bottom-1 -right-1 grid h-6 w-6 place-items-center rounded-full bg-card text-[10px] font-semibold text-purple-600 shadow">
                        {Math.round(percent)}%
                    </span>
                )}
                {latest.status === "running" && (latest.queue_length ?? 0) > 0 && (
                    <span className="absolute -left-1 -top-1 grid h-5 w-5 place-items-center rounded-full bg-red-500 text-[10px] font-semibold text-white shadow">
                        {latest.queue_length}
                    </span>
                )}
            </button>

            {/* 悬浮进度面板：桌面端在按钮左侧；移动端在按钮下方（避免互相遮挡） */}
            {open && (
                <div className="pointer-events-auto fixed right-3 top-[calc(50%+2.5rem)] z-40 w-[min(22rem,calc(100vw-1.5rem))] rounded-lg border bg-card p-4 shadow-xl md:right-20 md:top-1/2 md:w-[min(22rem,20rem)] md:-translate-y-1/2">
                    <div className="mb-2 flex items-center justify-between">
                        <div className="flex items-center gap-2 text-sm font-medium">
                            <Sparkles className="h-4 w-4 text-purple-500" />
                            {t("pose.taskProgress")}
                            {latest.trigger === "auto" && (
                                <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                                    auto
                                </span>
                            )}
                        </div>
                        <div className="flex items-center gap-2">
                            {latest.status === "running" && (
                                <Button variant="destructive" size="sm" onClick={() => stopMutation.mutate()}>
                                    <Square className="h-3.5 w-3.5" />
                                    {t("pose.stop")}
                                </Button>
                            )}
                            <Button variant="ghost" size="sm" onClick={() => setLogOpen((v) => !v)}>
                                {logOpen ? <X className="h-3.5 w-3.5" /> : t("pose.log")}
                            </Button>
                            <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
                                <X className="h-3.5 w-3.5" />
                            </Button>
                        </div>
                    </div>

                    <div className="space-y-2">
                        <div className="flex items-center justify-between text-sm">
                            <span className="truncate">
                                {latest.status === "running" && (
                                    <Loader2 className="mr-1 inline h-3 w-3 animate-spin" />
                                )}
                                {latest.message ?? statusLabel}
                            </span>
                            {latest.pending_files && latest.pending_files.length > 0 && (
                                <span
                                    className="shrink-0 text-muted-foreground"
                                    title={latest.pending_files.join("\n")}
                                >
                                    {tf("pose.waitingFiles", { count: latest.pending_files.length })}
                                </span>
                            )}
                            {latest.total_videos != null && latest.total_videos > 0 && (
                                <span className="shrink-0 text-muted-foreground">
                                    {tf("pose.videoProgress", {
                                        current: (latest.video_idx ?? 0) + 1,
                                        total: latest.total_videos,
                                    })}
                                </span>
                            )}
                        </div>

                        {latest.status === "running" && (
                            <Progress value={latest.total_percent ?? 0} className="h-2" />
                        )}

                        {latest.status === "completed" && latest.summary && (
                            <div className="text-xs text-muted-foreground">
                                {latest.summary.videos} videos · {latest.summary.segments} segments ·{" "}
                                {latest.summary.clips} clips
                            </div>
                        )}
                        {latest.queue_length ? (
                            <div className="text-xs text-muted-foreground">
                                +{latest.queue_length} queued
                            </div>
                        ) : null}
                    </div>

                    {logOpen && (
                        <pre className="mt-3 max-h-48 overflow-y-auto rounded-md bg-muted p-3 text-xs leading-relaxed">
                            {logText || "..."}
                        </pre>
                    )}
                </div>
            )}
        </div>
    )
}
