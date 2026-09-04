import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Activity, Loader2, Square, X } from "lucide-react"
import { useEffect, useState } from "react"
import { poseApi } from "@/api"
import type { PoseTaskState } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Progress } from "@/components/ui/progress"
import { useI18n, translateError } from "@/i18n"
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

/** 人体识别任务进度：视频管理工具栏内的按钮 + Popover 面板。

 * 与「人体识别」提交按钮区分：本按钮 ghost 样式、动态状态徽标
 * （运行中 spinner+百分比、队列数角标）。无任务时整个按钮隐藏。
 */
export function PoseTaskPanel() {
    const { t, tf } = useI18n()
    const queryClient = useQueryClient()
    const [open, setOpen] = useState(false)
    const [logOpen, setLogOpen] = useState(false)
    const [logText, setLogText] = useState("")

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

    const taskId = latest?.task_id

    // 新任务开始时清掉旧任务日志，并重置增量拉取的 offset
    useEffect(() => {
        setLogText("")
    }, [taskId])

    useEffect(() => {
        if (!logOpen || !taskId) return
        let offset = 0
        let cancelled = false
        const timer = setInterval(async () => {
            try {
                const result = await poseApi.log(taskId, offset)
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
    }, [logOpen, taskId])

    const stopMutation = useMutation({
        mutationFn: () => poseApi.stop(latest!.task_id!),
        onSuccess: () => toast.success(t("pose.stopped")),
        onError: (e: Error) => toast.error(translateError(e.message)),
        onSettled: () => queryClient.invalidateQueries({ queryKey: ["pose-tasks"] }),
    })

    // Popover 关闭即停日志轮询（否则组件未卸载时 interval 一直空转请求）
    const handleOpenChange = (next: boolean) => {
        setOpen(next)
        if (!next) setLogOpen(false)
    }

    if (!latest) {
        return null
    }

    const statusLabel = t(STATE_KEY[latest.state ?? ""] ?? STATUS_KEY[latest.status] ?? "pose.statusRunning")
    const percent = latest.total_percent ?? 0
    const isRunning = latest.status === "running"

    return (
        <Popover open={open} onOpenChange={handleOpenChange}>
            <PopoverTrigger asChild>
                <Button
                    variant="outline"
                    size="sm"
                    className={isRunning ? "border-primary/50 text-primary" : "text-muted-foreground"}
                    title={t("pose.taskProgress")}
                >
                    {isRunning ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                        <Activity className="h-4 w-4" />
                    )}
                    <span className="hidden sm:inline">{t("pose.taskProgress")}</span>
                    {isRunning && percent > 0 && (
                        <span className="rounded-full bg-primary px-1.5 text-[10px] font-semibold text-primary-foreground tabular-nums">
                            {Math.round(percent)}%
                        </span>
                    )}
                    {isRunning && (latest.queue_length ?? 0) > 0 && (
                        <span className="rounded-full bg-red-500 px-1.5 text-[10px] font-semibold text-white tabular-nums">
                            +{latest.queue_length}
                        </span>
                    )}
                </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="flex w-[calc(100vw-1.5rem)] flex-col overflow-hidden p-4 sm:w-[28rem]">
                <div className="mb-2 flex shrink-0 items-center justify-between">
                    <div className="flex min-w-0 items-center gap-2 text-sm font-medium">
                        <Activity className="h-4 w-4 shrink-0 text-primary" />
                        <span className="truncate">{t("pose.taskProgress")}</span>
                        {latest.trigger === "auto" && (
                            <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                                auto
                            </span>
                        )}
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                        {isRunning && (
                            <Button variant="destructive" size="sm" onClick={() => stopMutation.mutate()}>
                                <Square className="h-3.5 w-3.5" />
                                {t("pose.stop")}
                            </Button>
                        )}
                        <Button variant="ghost" size="sm" onClick={() => setLogOpen((v) => !v)}>
                            {t("pose.log")}
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
                            <X className="h-3.5 w-3.5" />
                        </Button>
                    </div>
                </div>

                <div className="shrink-0 space-y-2">
                    <div className="flex items-center justify-between gap-2 text-sm">
                        <span className="truncate">
                            {isRunning && <Loader2 className="mr-1 inline h-3 w-3 animate-spin" />}
                            {latest.message ?? statusLabel}
                        </span>
                        <span className="flex shrink-0 items-center gap-2 text-muted-foreground">
                            {latest.pending_files && latest.pending_files.length > 0 && (
                                <span title={latest.pending_files.join("\n")}>
                                    {tf("pose.waitingFiles", { count: latest.pending_files.length })}
                                </span>
                            )}
                            {latest.total_videos != null && latest.total_videos > 0 && (
                                <span>
                                    {tf("pose.videoProgress", {
                                        current: (latest.video_idx ?? 0) + 1,
                                        total: latest.total_videos,
                                    })}
                                </span>
                            )}
                        </span>
                    </div>

                    {isRunning && <Progress value={latest.total_percent ?? 0} className="h-2" />}

                    {latest.status === "completed" && latest.summary && (
                        <div className="text-xs text-muted-foreground">
                            {latest.summary.videos} videos · {latest.summary.segments} segments ·{" "}
                            {latest.summary.clips} clips
                        </div>
                    )}
                    {latest.queue_length ? (
                        <div className="text-xs text-muted-foreground">+{latest.queue_length} queued</div>
                    ) : null}
                </div>

                {logOpen && (
                    <pre className="mt-3 h-[50vh] max-h-[50vh] shrink-0 overflow-y-auto overscroll-contain rounded-md bg-muted p-3 text-xs leading-relaxed">
                        {logText || "..."}
                    </pre>
                )}
            </PopoverContent>
        </Popover>
    )
}
