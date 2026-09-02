import { useMutation, useQuery } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
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
import { useI18n } from "@/i18n"
import { toast } from "sonner"

const VIDEO_FORMATS = ["TS", "MP4", "FLV", "MKV", "MOV", "NUT"]
const AUDIO_FORMATS = ["WAV", "MP3", "WMA", "M4A", "AAC"]
const QUALITIES = ["OD", "UHD", "HD", "SD", "LD"]

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
    scheduled_start_time_1: string
    scheduled_start_time_2: string
    monitor_hours_1: string
    monitor_hours_2: string
    enabled_message_push: boolean
    only_notify_no_record: boolean
    pose_enabled: "" | "global" | "on" | "off"
    monitor_status: boolean
}

function initialState(rec: Recording | null, defaults: Record<string, unknown>): FormState {
    const scheduled = rec?.scheduled_start_time?.split(",") ?? []
    const hours = rec?.monitor_hours?.split(",") ?? []
    const mediaType =
        rec && !VIDEO_FORMATS.includes(rec.record_format) ? "audio" : "video"
    return {
        url: rec?.url ?? "",
        streamer_name: rec?.streamer_name ?? "",
        media_type: mediaType as "video" | "audio",
        record_format: rec?.record_format ?? String(defaults.video_format ?? "TS"),
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
        scheduled_start_time_1: scheduled[0]?.trim() ?? "",
        scheduled_start_time_2: scheduled[1]?.trim() ?? "",
        monitor_hours_1: hours[0]?.trim() ?? "5",
        monitor_hours_2: hours[1]?.trim() ?? "",
        enabled_message_push: rec?.enabled_message_push ?? false,
        only_notify_no_record: rec?.only_notify_no_record ?? false,
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
    onOpenChange,
    onSaved,
}: {
    open: boolean
    recording: Recording | null
    onOpenChange: (open: boolean) => void
    onSaved: () => void
}) {
    const { t } = useI18n()
    const [batchText, setBatchText] = useState("")
    const [form, setForm] = useState<FormState | null>(null)

    const { data: settingsData } = useQuery({
        queryKey: ["settings"],
        queryFn: settingsApi.get,
        enabled: open,
        staleTime: 30_000,
    })

    useEffect(() => {
        if (open) {
            setForm(initialState(recording, settingsData?.user_settings ?? {}))
            setBatchText("")
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
        onError: (e: Error) => toast.error(e.message),
    })

    const batchMutation = useMutation({
        mutationFn: (lines: string[]) => recordingsApi.createBatch(lines),
        onSuccess: (d) => {
            toast.success(t("recordings.addSuccess") + ` (${d.created})`)
            onOpenChange(false)
            onSaved()
        },
        onError: (e: Error) => toast.error(e.message),
    })

    const updateMutation = useMutation({
        mutationFn: (body: Record<string, unknown>) =>
            recordingsApi.update(recording!.rec_id, body as Partial<Recording>),
        onSuccess: () => {
            toast.success(t("common.success"))
            onOpenChange(false)
            onSaved()
        },
        onError: (e: Error) => toast.error(e.message),
    })

    const submitSingle = () => {
        if (!form) return
        const url = form.url.trim()
        if (!url) {
            toast.error(t("recordingDialog.urlRequired"))
            return
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

        const scheduled = [form.scheduled_start_time_1, form.scheduled_start_time_2]
            .filter(Boolean)
            .join(",")
        const hours = [form.monitor_hours_1, form.monitor_hours_2].filter(Boolean).join(",")

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
            <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
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
    onChange,
    onSubmit,
    pending,
}: {
    form: FormState
    formats: string[]
    onChange: <K extends keyof FormState>(key: K, value: FormState[K]) => void
    onSubmit: () => void
    pending: boolean
}) {
    const { t } = useI18n()
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
                <Input
                    value={form.url}
                    onChange={(e) => onChange("url", e.target.value)}
                    placeholder={t("recordingDialog.urlPlaceholder")}
                />
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
                            onChange("record_format", type === "video" ? "TS" : "WAV")
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
                    <Label>{t("recordingDialog.scheduled")}</Label>
                    <Checkbox
                        checked={form.scheduled_recording}
                        onCheckedChange={(v) => onChange("scheduled_recording", Boolean(v))}
                    />
                </div>
                {form.scheduled_recording && (
                    <div className="grid grid-cols-2 gap-3">
                        {[1, 2].map((i) => (
                            <div key={i} className="space-y-1.5">
                                <Label className="text-xs text-muted-foreground">
                                    {i === 1
                                        ? t("recordingDialog.scheduledTime1")
                                        : t("recordingDialog.scheduledTime2")}
                                </Label>
                                <Input
                                    type="time"
                                    step="1"
                                    value={form[`scheduled_start_time_${i}` as keyof FormState] as string}
                                    onChange={(e) =>
                                        onChange(
                                            `scheduled_start_time_${i}` as keyof FormState,
                                            e.target.value,
                                        )
                                    }
                                />
                                <Input
                                    type="number"
                                    placeholder={t("recordingDialog.monitorHours")}
                                    value={form[`monitor_hours_${i}` as keyof FormState] as string}
                                    onChange={(e) =>
                                        onChange(`monitor_hours_${i}` as keyof FormState, e.target.value)
                                    }
                                />
                            </div>
                        ))}
                    </div>
                )}
            </div>

            <div className="grid grid-cols-2 gap-2">
                {(
                    [
                        ["enabled_message_push", "recordingDialog.messagePush"],
                        ["only_notify_no_record", "recordingDialog.onlyNotify"],
                        ["monitor_status", "recordings.startMonitor"],
                    ] as const
                ).map(([key, label]) => (
                    <div key={key} className="flex items-center gap-2">
                        <Checkbox
                            id={key}
                            checked={form[key] as boolean}
                            onCheckedChange={(v) => onChange(key, Boolean(v))}
                        />
                        <Label htmlFor={key} className="cursor-pointer text-sm font-normal">
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
