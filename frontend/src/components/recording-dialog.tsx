import { useMutation, useQuery } from "@tanstack/react-query"
import { Loader2, Plus, X } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { recordingsApi, settingsApi } from "@/api"
import type { Recording } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { useI18n , translateError } from "@/i18n"
import {
    crossesMidnight,
    endFromHours,
    formatHours,
    hoursFromRange,
    parseClock,
} from "@/lib/schedule"
import { toast } from "sonner"

const VIDEO_FORMATS = ["TS", "MP4", "FLV", "MKV", "MOV", "NUT"]
const AUDIO_FORMATS = ["WAV", "MP3", "WMA", "M4A", "AAC"]
const QUALITIES = ["OD", "UHD", "HD", "SD", "LD"]

/** 「每日定时监听」一个时间段：界面填开始/结束，保存时换算成时长 */
interface ScheduleWindow {
    start: string
    end: string
}

/** 时间段上限：再多就是把一天切碎，等于全天监控，还会让展示和排障都变乱 */
const MAX_SCHEDULE_WINDOWS = 8

const EMPTY_WINDOW: ScheduleWindow = { start: "", end: "" }

/** 没配过时间段时的默认窗口（07:00~21:00，14 小时） */
const DEFAULT_WINDOW: ScheduleWindow = { start: "07:00:00", end: "21:00:00" }

const URL_PLATFORMS = [
    { key: "douyin", label: "抖音直播", base: "https://live.douyin.com/" },
    { key: "huya", label: "虎牙直播", base: "https://www.huya.com/" },
    { key: "douyu", label: "斗鱼直播", base: "https://www.douyu.com/" },
    { key: "bilibili", label: "B站直播", base: "https://live.bilibili.com/" },
    { key: "kuaishou", label: "快手直播", base: "https://live.kuaishou.com/u/" },
    { key: "yy", label: "YY直播", base: "https://www.yy.com/" },
]

interface FormState {
    url: string
    streamer_name: string
    media_type: "video" | "audio"
    record_format: string
    quality: string
    video_bitrate: string
    flv_use_direct_download: boolean
    recording_dir: string
    segment_record: boolean
    segment_time: string
    scheduled_recording: boolean
    // 可变长度：界面填「开始 ~ 结束」，保存时换算成后端的 monitor_hours（时长）
    schedule_windows: ScheduleWindow[]
    enabled_message_push: boolean
    only_notify_no_record: boolean
    special_attention: boolean
    pose_enabled: "" | "global" | "on" | "off"
    monitor_status: boolean
}

/**
 * 「开始,…」+「时长,…」→ 界面用的窗口数组。
 * 新建（没有历史配置）默认给**一段 07:00~21:00**；老数据有几段还原几段，不做截断。
 */
function toWindows(starts: string[], hours: string[]): ScheduleWindow[] {
    const windows = starts
        .map((start, i) => ({
            start,
            end: start ? endFromHours(start, hours[i] || "5") : "",
        }))
        .filter((w) => w.start || w.end)
    return windows.length ? windows : [{ ...DEFAULT_WINDOW }]
}

function initialState(rec: Recording | null, defaults: Record<string, unknown>): FormState {
    // 老数据是「开始,开始,…」+「时长,时长,…」按索引配对，全量还原（不截断段数）
    const starts = (rec?.scheduled_start_time ?? "").split(",").map((x) => x.trim())
    const hours = (rec?.monitor_hours ?? "").split(",").map((x) => x.trim())
    const mediaType =
        rec && !VIDEO_FORMATS.includes(rec.record_format) ? "audio" : "video"
    return {
        url: rec?.url ?? "",
        streamer_name: rec?.streamer_name ?? "",
        media_type: mediaType as "video" | "audio",
        record_format: rec?.record_format ?? String(defaults.video_format ?? "MP4"),
        quality: rec?.quality ?? String(defaults.record_quality ?? "OD"),
        video_bitrate: rec?.video_bitrate ? String(rec.video_bitrate) : "",
        flv_use_direct_download: rec
            ? Boolean(rec.flv_use_direct_download)
            : Boolean(defaults.flv_use_direct_download ?? false),
        recording_dir: rec?.recording_dir ?? "",
        segment_record: rec
            ? Boolean(rec.segment_record)
            : Boolean(defaults.segmented_recording_enabled ?? false),
        segment_time: String(rec?.segment_time ?? defaults.video_segment_time ?? "1800"),
        scheduled_recording: rec?.scheduled_recording ?? false,
        schedule_windows: toWindows(starts, hours),
        enabled_message_push: rec?.enabled_message_push ?? false,
        only_notify_no_record: rec?.only_notify_no_record ?? false,
        special_attention: rec?.special_attention ?? false,
        pose_enabled:
            rec == null
                ? "global"
                : rec.pose_enabled == null
                  ? "global"
                  : rec.pose_enabled
                    ? "on"
                    : "off",
        monitor_status: rec?.monitor_status ?? true,
    }
}

export function RecordingDialog({
    open,
    recording,
    preset,
    onOpenChange,
    onSaved,
}: {
    open: boolean
    recording: Recording | null
    /** 新建时的预填（如从「关注」页一键监控某个正在直播的主播）；编辑模式无效 */
    preset?: { url: string; streamer_name: string } | null
    onOpenChange: (open: boolean) => void
    onSaved: () => void
}) {
    const { t } = useI18n()
    const [batchText, setBatchText] = useState("")
    const [form, setForm] = useState<FormState | null>(null)
    const [urlPlatform, setUrlPlatform] = useState(URL_PLATFORMS[0].key)

    const { data: settingsData } = useQuery({
        queryKey: ["settings"],
        queryFn: settingsApi.get,
        // 页面挂载即预取：若等弹窗打开才拉，首次打开时表单会按空 defaults 初始化
        // （分段录制等开关落成关），且 effect 不随数据到达重跑，开关就错下去了
        enabled: true,
        staleTime: 30_000,
    })

    useEffect(() => {
        if (open) {
            const initial = initialState(recording, settingsData?.user_settings ?? {})
            if (!recording && preset) {
                initial.url = preset.url
                initial.streamer_name = preset.streamer_name
            }
            setForm(initial)
            setBatchText("")
            setUrlPlatform(URL_PLATFORMS[0].key)
        }
    }, [open, recording]) // eslint-disable-line react-hooks/exhaustive-deps

    const set = <K extends keyof FormState>(key: K, value: FormState[K]) => {
        setForm((f) => (f ? { ...f, [key]: value } : f))
    }

    const formats = form?.media_type === "audio" ? AUDIO_FORMATS : VIDEO_FORMATS

    const createMutation = useMutation({
        mutationFn: (body: Record<string, unknown>) => recordingsApi.create(body as Partial<Recording>),
        onSuccess: () => {
            toast.success(t("recordings.addSuccess"))
            onOpenChange(false)
            onSaved()
        },
        onError: (e: Error) => toast.error(translateError(e.message)),
    })

    const batchMutation = useMutation({
        mutationFn: (lines: string[]) => recordingsApi.createBatch(lines),
        onSuccess: (d) => {
            toast.success(t("recordings.addSuccess") + ` (${d.created})`)
            onOpenChange(false)
            onSaved()
        },
        onError: (e: Error) => toast.error(translateError(e.message)),
    })

    const updateMutation = useMutation({
        mutationFn: (body: Record<string, unknown>) =>
            recordingsApi.update(recording!.rec_id, body as Partial<Recording>),
        onSuccess: () => {
            toast.success(t("common.success"))
            onOpenChange(false)
            onSaved()
        },
        onError: (e: Error) => toast.error(translateError(e.message)),
    })

    const submitSingle = () => {
        if (!form) return
        const rawUrl = form.url.trim()
        if (!rawUrl) {
            toast.error(t("recordingDialog.urlRequired"))
            return
        }
        // 平台模式下输入的是房间号，拼上所选平台的基础地址
        let url = rawUrl
        if (!/^https?:\/\//.test(rawUrl)) {
            const platform = URL_PLATFORMS.find((p) => p.key === urlPlatform)
            if (!platform) {
                toast.error(t("recordingDialog.invalidUrl"))
                return
            }
            url = platform.base + rawUrl.replace(/^\/+/, "")
        }
        if (!/^https?:\/\//.test(url)) {
            toast.error(t("recordingDialog.invalidUrl"))
            return
        }
        let videoBitrate: number | null = null
        if (form.video_bitrate.trim()) {
            const v = parseInt(form.video_bitrate, 10)
            if (!Number.isInteger(v) || v <= 0) {
                toast.error(t("recordingDialog.invalidBitrate"))
                return
            }
            videoBitrate = v
        }

        // 界面填「开始 ~ 结束」，存回后端仍是 monitor_hours（时长）。
        // 结束早于开始按次日算（23:00 → 02:00 = 3h）；两者相等等于不监控，直接拦。
        // 定时没开就整段跳过校验、也不存窗口，别留一份「看不见的配置」。
        const starts: string[] = []
        const hoursList: string[] = []
        if (form.scheduled_recording) {
            for (const { start, end } of form.schedule_windows) {
                if (!start && !end) continue // 空行（用户点了「添加时间段」没填）直接忽略
                if (!start || !end) {
                    toast.error(t("recordingDialog.scheduledEndRequired"))
                    return
                }
                if (parseClock(start) == null || parseClock(end) == null) {
                    toast.error(t("recordingDialog.scheduledInvalid"))
                    return
                }
                const hours = hoursFromRange(start, end)
                if (hours == null) {
                    toast.error(t("recordingDialog.scheduledEqual"))
                    return
                }
                starts.push(start)
                hoursList.push(String(hours))
            }
        }
        const scheduled = starts.join(",")
        const hours = hoursList.join(",")

        const body: Record<string, unknown> = {
            url,
            streamer_name: form.streamer_name.trim(),
            record_format: form.record_format,
            quality: form.quality,
            video_bitrate: videoBitrate,
            flv_use_direct_download: form.flv_use_direct_download,
            recording_dir: form.recording_dir.trim() || null,
            segment_record: form.segment_record,
            segment_time: parseInt(form.segment_time, 10) || 1800,
            scheduled_recording: form.scheduled_recording,
            scheduled_start_time: scheduled || null,
            monitor_hours: hours || null,
            enabled_message_push: form.enabled_message_push,
            only_notify_no_record: form.only_notify_no_record,
            special_attention: form.special_attention,
            monitor_status: form.monitor_status,
            pose_enabled:
                form.pose_enabled === "global"
                    ? null
                    : form.pose_enabled === "on",
        }
        if (recording) updateMutation.mutate(body)
        else createMutation.mutate(body)
    }

    const submitBatch = () => {
        const lines = batchText
            .split("\n")
            .map((l) => l.trim())
            .filter((l) => l && l.includes("http"))
        if (lines.length === 0) {
            toast.error(t("recordingDialog.urlRequired"))
            return
        }
        batchMutation.mutate(lines)
    }

    const pending =
        createMutation.isPending || updateMutation.isPending || batchMutation.isPending

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-h-[92dvh] overflow-y-auto p-4 sm:max-w-2xl sm:p-6">
                <DialogHeader>
                    <DialogTitle>
                        {recording ? t("recordingDialog.editTitle") : t("recordingDialog.addTitle")}
                    </DialogTitle>
                </DialogHeader>

                {!recording ? (
                    <Tabs defaultValue="single">
                        <TabsList>
                            <TabsTrigger value="single">{t("recordingDialog.single")}</TabsTrigger>
                            <TabsTrigger value="batch">{t("recordingDialog.batch")}</TabsTrigger>
                        </TabsList>
                        <TabsContent value="single">
                            {form && (
                                <SingleForm
                                    form={form}
                                    formats={formats}
                                    showPlatformSelector
                                    urlPlatform={urlPlatform}
                                    onUrlPlatform={setUrlPlatform}
                                    onChange={set}
                                    onSubmit={submitSingle}
                                    pending={pending}
                                />
                            )}
                        </TabsContent>
                        <TabsContent value="batch" className="space-y-3">
                            <Textarea
                                rows={10}
                                value={batchText}
                                onChange={(e) => setBatchText(e.target.value)}
                                placeholder={t("recordingDialog.batchPlaceholder")}
                                className="font-mono text-sm"
                            />
                            <Button onClick={submitBatch} disabled={pending} className="w-full">
                                {pending && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
                                {t("common.confirm")}
                            </Button>
                        </TabsContent>
                    </Tabs>
                ) : form ? (
                    <SingleForm
                        form={form}
                        formats={formats}
                        onChange={set}
                        onSubmit={submitSingle}
                        pending={pending}
                    />
                ) : null}
            </DialogContent>
        </Dialog>
    )
}

function SingleForm({
    form,
    formats,
    showPlatformSelector = false,
    urlPlatform,
    onUrlPlatform,
    onChange,
    onSubmit,
    pending,
}: {
    form: FormState
    formats: string[]
    showPlatformSelector?: boolean
    urlPlatform?: string
    onUrlPlatform?: (key: string) => void
    onChange: <K extends keyof FormState>(key: K, value: FormState[K]) => void
    onSubmit: () => void
    pending: boolean
}) {
    const { t, tf } = useI18n()
    const onWindowsChange = (windows: ScheduleWindow[]) => {
        // 至少留一行空行，否则整块 UI 会消失、用户不知道去哪加回来
        onChange("schedule_windows", windows.length ? windows : [{ ...EMPTY_WINDOW }])
    }
    return (
        <form
            className="space-y-4"
            onSubmit={(e) => {
                e.preventDefault()
                onSubmit()
            }}
        >
            <div className="space-y-1.5">
                <Label>{t("recordingDialog.url")} *</Label>
                {showPlatformSelector && (
                    <Select
                        value={urlPlatform ?? URL_PLATFORMS[0].key}
                        onValueChange={(v) => onUrlPlatform?.(v)}
                    >
                        <SelectTrigger>
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            {URL_PLATFORMS.map((p) => (
                                <SelectItem key={p.key} value={p.key}>
                                    {p.label}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                )}
                {(() => {
                    const platform = URL_PLATFORMS.find((p) => p.key === urlPlatform)
                    const isFullUrl = /^https?:\/\//.test(form.url.trim())
                    const showPrefix =
                        showPlatformSelector && !!platform && !isFullUrl
                    return (
                        <div className="flex">
                            {showPrefix && (
                                <span className="inline-flex max-w-[55%] items-center truncate rounded-l-md border border-r-0 border-input bg-muted px-2 text-xs text-muted-foreground">
                                    {platform!.base}
                                </span>
                            )}
                            <Input
                                value={form.url}
                                onChange={(e) => onChange("url", e.target.value)}
                                placeholder={
                                    showPrefix
                                        ? t("recordingDialog.roomIdPlaceholder")
                                        : t("recordingDialog.urlPlaceholder")
                                }
                                className={showPrefix ? "rounded-l-none" : ""}
                            />
                        </div>
                    )
                })()}
            </div>

            <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                    <Label>{t("recordingDialog.streamerName")}</Label>
                    <Input
                        value={form.streamer_name}
                        onChange={(e) => onChange("streamer_name", e.target.value)}
                        placeholder={t("recordingDialog.streamerNamePlaceholder")}
                    />
                </div>
                <div className="space-y-1.5">
                    <Label>{t("recordingDialog.quality")}</Label>
                    <Select value={form.quality} onValueChange={(v) => onChange("quality", v)}>
                        <SelectTrigger>
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            {QUALITIES.map((q) => (
                                <SelectItem key={q} value={q}>
                                    {t(`quality.${q}`)}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                    <Label>{t("recordingDialog.mediaType")}</Label>
                    <Select
                        value={form.media_type}
                        onValueChange={(v) => {
                            const type = v as "video" | "audio"
                            onChange("media_type", type)
                            onChange("record_format", type === "video" ? "MP4" : "WAV")
                        }}
                    >
                        <SelectTrigger>
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="video">{t("recordingDialog.video")}</SelectItem>
                            <SelectItem value="audio">{t("recordingDialog.audio")}</SelectItem>
                        </SelectContent>
                    </Select>
                </div>
                <div className="space-y-1.5">
                    <Label>{t("recordingDialog.recordFormat")}</Label>
                    <Select value={form.record_format} onValueChange={(v) => onChange("record_format", v)}>
                        <SelectTrigger>
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            {formats.map((f) => (
                                <SelectItem key={f} value={f}>
                                    {f}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>
            </div>

            {form.media_type === "video" && (
                <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1.5">
                        <Label>{t("recordingDialog.videoBitrate")}</Label>
                        <Input
                            type="number"
                            value={form.video_bitrate}
                            onChange={(e) => onChange("video_bitrate", e.target.value)}
                            placeholder={t("recordingDialog.videoBitrateHint")}
                        />
                    </div>
                    <div className="flex items-end gap-2 pb-1">
                        <Checkbox
                            id="flv-direct"
                            checked={form.flv_use_direct_download}
                            onCheckedChange={(v) => onChange("flv_use_direct_download", Boolean(v))}
                        />
                        <Label htmlFor="flv-direct" className="cursor-pointer text-sm font-normal">
                            {t("recordingDialog.flvDirectDownload")}
                        </Label>
                    </div>
                </div>
            )}

            <div className="space-y-1.5">
                <Label>{t("recordingDialog.savePath")}</Label>
                <Input
                    value={form.recording_dir}
                    onChange={(e) => onChange("recording_dir", e.target.value)}
                    placeholder={t("recordingDialog.savePathPlaceholder")}
                />
            </div>

            <div className="space-y-2 rounded-md border p-3">
                <div className="flex items-center justify-between">
                    <Label>{t("recordingDialog.segment")}</Label>
                    <Checkbox
                        checked={form.segment_record}
                        onCheckedChange={(v) => onChange("segment_record", Boolean(v))}
                    />
                </div>
                {form.segment_record && (
                    <div className="space-y-1.5">
                        <Label className="text-xs text-muted-foreground">
                            {t("recordingDialog.segmentTime")}
                        </Label>
                        <Input
                            type="number"
                            value={form.segment_time}
                            onChange={(e) => onChange("segment_time", e.target.value)}
                        />
                    </div>
                )}
            </div>

            <div className="space-y-2 rounded-md border p-3">
                <div className="flex items-center justify-between">
                    <Label htmlFor="scheduled-recording" className="cursor-pointer text-sm font-medium">
                        {t("recordingDialog.scheduled")}
                    </Label>
                    <Checkbox
                        id="scheduled-recording"
                        checked={form.scheduled_recording}
                        onCheckedChange={(v) => onChange("scheduled_recording", Boolean(v))}
                    />
                </div>
                {form.scheduled_recording && (
                    <>
                        <div className="space-y-2" data-testid="scheduled-windows">
                            {form.schedule_windows.map((win, idx) => {
                                const start = win.start
                                const end = win.end
                                const hours = hoursFromRange(start, end)
                                return (
                                    <div
                                        key={idx}
                                        className="rounded-md border border-dashed p-2"
                                        data-testid={`scheduled-window-${idx + 1}`}
                                    >
                                        <div className="mb-1 flex items-center justify-between">
                                            <Label className="text-xs text-muted-foreground">
                                                {tf("recordingDialog.scheduledTimeN", { n: idx + 1 })}
                                            </Label>
                                            <Button
                                                type="button"
                                                variant="ghost"
                                                size="icon"
                                                className="h-5 w-5 text-muted-foreground"
                                                title={t("recordingDialog.scheduledRemove")}
                                                aria-label={t("recordingDialog.scheduledRemove")}
                                                data-testid={`scheduled-remove-${idx + 1}`}
                                                onClick={() => onWindowsChange(
                                                    form.schedule_windows.filter((_, i) => i !== idx),
                                                )}
                                            >
                                                <X className="h-3.5 w-3.5" />
                                            </Button>
                                        </div>
                                        <div className="flex items-center gap-1.5">
                                            <Input
                                                type="time"
                                                step="1"
                                                aria-label={t("recordingDialog.scheduledStart")}
                                                value={start}
                                                onChange={(e) => {
                                                    const value = e.target.value
                                                    // 先填开始：结束空着就按默认 5 小时预填，省一次输入
                                                    onWindowsChange(
                                                        form.schedule_windows.map((w, i) =>
                                                            i === idx
                                                                ? {
                                                                      start: value,
                                                                      end:
                                                                          w.end ||
                                                                          (value
                                                                              ? endFromHours(value, "5")
                                                                              : ""),
                                                                  }
                                                                : w,
                                                        ),
                                                    )
                                                }}
                                            />
                                            <span className="shrink-0 text-muted-foreground">~</span>
                                            <Input
                                                type="time"
                                                step="1"
                                                aria-label={t("recordingDialog.scheduledEnd")}
                                                value={end}
                                                onChange={(e) =>
                                                    onWindowsChange(
                                                        form.schedule_windows.map((w, i) =>
                                                            i === idx ? { ...w, end: e.target.value } : w,
                                                        ),
                                                    )
                                                }
                                            />
                                        </div>
                                        <p
                                            className="text-xs text-muted-foreground"
                                            data-testid={`scheduled-duration-${idx + 1}`}
                                        >
                                            {hours == null
                                                ? ""
                                                : tf("recordingDialog.scheduledDuration", {
                                                      hours: formatHours(hours),
                                                  }) +
                                                  (crossesMidnight(start, end)
                                                      ? ` · ${t("recordingDialog.scheduledNextDay")}`
                                                      : "")}
                                        </p>
                                    </div>
                                )
                            })}
                        </div>
                        <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            className="w-full"
                            data-testid="scheduled-add"
                            disabled={form.schedule_windows.length >= MAX_SCHEDULE_WINDOWS}
                            onClick={() =>
                                onWindowsChange([...form.schedule_windows, { ...EMPTY_WINDOW }])
                            }
                        >
                            <Plus className="mr-1 h-3.5 w-3.5" />
                            {tf("recordingDialog.scheduledAdd", { max: MAX_SCHEDULE_WINDOWS })}
                        </Button>
                        <p className="text-xs text-muted-foreground">
                            {t("recordingDialog.scheduledHint")}
                        </p>
                    </>
                )}
            </div>

            <div className="grid grid-cols-2 gap-2">
                {(
                    [
                        ["enabled_message_push", "recordingDialog.messagePush"],
                        ["only_notify_no_record", "recordingDialog.onlyNotify"],
                        ["special_attention", "recordingDialog.specialAttention"],
                        ["monitor_status", "recordings.startMonitor"],
                    ] as const
                ).map(([key, label]) => (
                    <div key={key} className="flex items-center gap-2">
                        <Checkbox
                            id={key}
                            checked={form[key] as boolean}
                            onCheckedChange={(v) => onChange(key, Boolean(v))}
                        />
                        <Label
                            htmlFor={key}
                            className="cursor-pointer text-sm font-normal"
                            title={key === "special_attention" ? t("recordingDialog.specialAttentionHint") : undefined}
                        >
                            {t(label)}
                        </Label>
                    </div>
                ))}
            </div>

            <div className="space-y-1.5">
                <Label>{t("recordingDialog.poseDetection")}</Label>
                <Select
                    value={form.pose_enabled}
                    onValueChange={(v) => onChange("pose_enabled", v as FormState["pose_enabled"])}
                >
                    <SelectTrigger>
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="global">{t("recordingDialog.poseFollowGlobal")}</SelectItem>
                        <SelectItem value="on">{t("recordingDialog.poseEnabled")}</SelectItem>
                        <SelectItem value="off">{t("recordingDialog.poseDisabled")}</SelectItem>
                    </SelectContent>
                </Select>
            </div>

            <Button type="submit" className="w-full" disabled={pending}>
                {pending && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
                {t("common.confirm")}
            </Button>
        </form>
    )
}
