import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { CheckCircle2, ChevronDown, ExternalLink, Loader2, Pause, Play, QrCode, RefreshCw, RotateCcw, Trash2, XCircle } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { douyinApi, settingsApi, uploadsApi } from "@/api"
import type {
    DouyinCookieStatus,
    SettingsData,
    SettingsResetResult,
    SettingsWriteResult,
    UploadJob,
} from "@/api/types"
import { BilibiliLoginDialog } from "@/components/bilibili-login-dialog"
import { useConfirm } from "@/components/confirm-dialog"
import { DouyinLoginDialog } from "@/components/douyin-login-dialog"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { formatDate, formatDateTime, translateError, useI18n, type LangCode } from "@/i18n"
import { toast } from "sonner"

export default function SettingsPage() {
    const { t } = useI18n()
    return (
        // 设置页整页随主区滚动（内容比 h-full 的 wrapper 高），而 main 的底部安全区只覆盖 wrapper，
        // 所以这里自己补一份，否则滚到底时最后一段（如上传记录分页条）会被手机底部导航压住
        <Tabs defaultValue="recording" className="space-y-4 pb-[calc(4rem+env(safe-area-inset-bottom))] md:pb-0">
            <TabsList className="w-full overflow-x-auto whitespace-nowrap sm:w-auto">
                <TabsTrigger value="recording">录制设置</TabsTrigger>
                <TabsTrigger value="pose">人体识别</TabsTrigger>
                <TabsTrigger value="uploads">{t("settings.uploads")}</TabsTrigger>
                <TabsTrigger value="push">推送设置</TabsTrigger>
                <TabsTrigger value="cookies">Cookies</TabsTrigger>
                <TabsTrigger value="accounts">账号设置</TabsTrigger>
                <TabsTrigger value="security">安全设置</TabsTrigger>
            </TabsList>
            <TabsContent value="recording">
                <RecordingSettings />
            </TabsContent>
            <TabsContent value="pose">
                <PoseSettings />
            </TabsContent>
            <TabsContent value="uploads">
                <UploadSettings />
            </TabsContent>
            <TabsContent value="push">
                <PushSettings />
            </TabsContent>
            <TabsContent value="cookies">
                <CookiesTab />
            </TabsContent>
            <TabsContent value="accounts">
                <AccountsTab />
            </TabsContent>
            <TabsContent value="security">
                <SecurityTab />
            </TabsContent>
        </Tabs>
    )
}

interface ChannelDef {
    key: string
    labelKey: string
    enabledKey: string
    fields: Array<{ key: string; labelKey: string; type: FieldType; options?: Array<{ value: string; label: string }> }>
}

const PUSH_CHANNELS: ChannelDef[] = [
    {
        key: "dingtalk",
        labelKey: "settings.channelDingtalk",
        enabledKey: "dingtalk_enabled",
        fields: [
            { key: "dingtalk_webhook_url", labelKey: "settings.webhookUrl", type: "text" },
            { key: "dingtalk_at_objects", labelKey: "settings.atObjects", type: "text" },
            { key: "dingtalk_at_all", labelKey: "settings.atAll", type: "switch" },
        ],
    },
    {
        key: "wechat",
        labelKey: "settings.channelWechat",
        enabledKey: "wechat_enabled",
        fields: [{ key: "wechat_webhook_url", labelKey: "settings.webhookUrl", type: "text" }],
    },
    {
        key: "feishu",
        labelKey: "settings.channelFeishu",
        enabledKey: "feishu_enabled",
        fields: [{ key: "feishu_webhook_url", labelKey: "settings.webhookUrl", type: "text" }],
    },
    {
        key: "serverchan",
        labelKey: "settings.channelServerchan",
        enabledKey: "serverchan_enabled",
        fields: [
            { key: "serverchan_sendkey", labelKey: "settings.sendkey", type: "text" },
            { key: "serverchan_channel", labelKey: "settings.channel", type: "text" },
            { key: "serverchan_tags", labelKey: "settings.tags", type: "text" },
        ],
    },
    {
        key: "bark",
        labelKey: "settings.channelBark",
        enabledKey: "bark_enabled",
        fields: [
            { key: "bark_webhook_url", labelKey: "settings.webhookUrl", type: "text" },
            {
                key: "bark_interrupt_level",
                labelKey: "settings.interruptLevel",
                type: "select",
                options: [
                    { value: "active", label: "active" },
                    { value: "passive", label: "passive" },
                ],
            },
            { key: "bark_sound", labelKey: "settings.sound", type: "text" },
        ],
    },
    {
        key: "ntfy",
        labelKey: "settings.channelNtfy",
        enabledKey: "ntfy_enabled",
        fields: [
            { key: "ntfy_server_url", labelKey: "settings.serverUrl", type: "text" },
            { key: "ntfy_tags", labelKey: "settings.tags", type: "text" },
            { key: "ntfy_email", labelKey: "settings.email", type: "text" },
            { key: "ntfy_action_url", labelKey: "settings.actionUrl", type: "text" },
        ],
    },
    {
        key: "telegram",
        labelKey: "settings.channelTelegram",
        enabledKey: "telegram_enabled",
        fields: [
            { key: "telegram_api_token", labelKey: "settings.apiToken", type: "text" },
            { key: "telegram_chat_id", labelKey: "settings.chatId", type: "text" },
        ],
    },
    {
        key: "email",
        labelKey: "settings.channelEmail",
        enabledKey: "email_enabled",
        fields: [
            { key: "smtp_server", labelKey: "settings.smtpServer", type: "text" },
            { key: "email_username", labelKey: "settings.emailUsername", type: "text" },
            { key: "email_password", labelKey: "settings.emailPassword", type: "text" },
            { key: "sender_email", labelKey: "settings.senderEmail", type: "text" },
            { key: "sender_name", labelKey: "settings.senderName", type: "text" },
            { key: "recipient_email", labelKey: "settings.recipientEmail", type: "text" },
        ],
    },
]

function PushSettings() {
    const { t } = useI18n()
    const form = useSettingsForm()

    if (form.isLoading || !form.data) {
        return (
            <div className="flex justify-center py-20">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
        )
    }

    const generalFields: FieldDef[] = [
        { key: "stream_start_notification_enabled", label: t("settings.streamStartPush"), type: "switch" },
        { key: "stream_end_notification_enabled", label: t("settings.streamEndPush"), type: "switch" },
        { key: "only_notify_no_record", label: t("settings.onlyNotifyNoRecord"), type: "switch" },
        { key: "notify_loop_time", label: t("settings.notifyLoopTime"), type: "number" },
        { key: "custom_notification_title", label: t("settings.customTitle"), type: "text" },
        { key: "custom_stream_start_content", label: t("settings.customStartContent"), type: "text" },
        { key: "custom_stream_end_content", label: t("settings.customEndContent"), type: "text" },
    ]

    return (
        <div className="space-y-4">
            <div className="rounded-lg border bg-card p-4">
                <h3 className="mb-3 font-semibold">{t("settings.notificationGeneral")}</h3>
                <div className="grid grid-cols-1 gap-x-6 gap-y-4 md:grid-cols-2">
                    {generalFields.map((field) => (
                        <FieldRow key={field.key} field={field} form={form} />
                    ))}
                </div>
            </div>

            {PUSH_CHANNELS.map((channel) => (
                <div key={channel.key} className="rounded-lg border bg-card p-4">
                    <div className="mb-3 flex items-center justify-between">
                        <h3 className="font-semibold">{t(channel.labelKey)}</h3>
                        <Switch
                            checked={Boolean(form.values[channel.enabledKey])}
                            onCheckedChange={(v) => form.set(channel.enabledKey, v)}
                        />
                    </div>
                    <div className="grid grid-cols-1 gap-x-6 gap-y-4 md:grid-cols-2">
                        {channel.fields.map((field) => (
                            <FieldRow
                                key={field.key}
                                field={{ ...field, label: t(field.labelKey) }}
                                form={form}
                            />
                        ))}
                    </div>
                </div>
            ))}
        </div>
    )
}

const ACCOUNT_PLATFORMS: Array<{
    key: string
    label: string
    fields: Array<{ key: string; labelKey: string; type: FieldType; options?: Array<{ value: string; label: string }> }>
}> = [
    {
        key: "sooplive",
        label: "SOOP Live",
        fields: [
            { key: "sooplive_username", labelKey: "settings.accountUsername", type: "text" },
            { key: "sooplive_password", labelKey: "settings.accountPassword", type: "text" },
        ],
    },
    {
        key: "flextv",
        label: "FlexTV",
        fields: [
            { key: "flextv_username", labelKey: "settings.accountUsername", type: "text" },
            { key: "flextv_password", labelKey: "settings.accountPassword", type: "text" },
        ],
    },
    {
        key: "popkontv",
        label: "PopKonTV",
        fields: [
            { key: "popkontv_username", labelKey: "settings.accountUsername", type: "text" },
            { key: "popkontv_password", labelKey: "settings.accountPassword", type: "text" },
        ],
    },
    {
        key: "twitcasting",
        label: "Twitcasting",
        fields: [
            { key: "twitcasting_username", labelKey: "settings.accountUsername", type: "text" },
            { key: "twitcasting_password", labelKey: "settings.accountPassword", type: "text" },
            {
                key: "twitcasting_account_type",
                labelKey: "settings.accountType",
                type: "select",
                options: [
                    { value: "Default", label: "Default" },
                    { value: "Twitter", label: "Twitter" },
                ],
            },
        ],
    },
]

function AccountsTab() {
    const { t } = useI18n()
    const { data, isLoading } = useQuery({ queryKey: ["accounts"], queryFn: settingsApi.getAccounts })

    const [values, setValues] = useState<Record<string, Record<string, string>>>({})
    const save = useMutation({
        mutationFn: (accounts: Record<string, Record<string, string>>) =>
            settingsApi.updateAccounts(accounts),
        onSuccess: () => toast.success(t("settings.saved")),
        onError: () => toast.error(t("settings.saveFailed")),
    })

    useEffect(() => {
        if (data) setValues(data.accounts)
    }, [data])

    if (isLoading) {
        return (
            <div className="flex justify-center py-20">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
        )
    }

    const setField = (platform: string, field: string, value: string) =>
        setValues((v) => ({ ...v, [platform]: { ...(v[platform] ?? {}), [field]: value } }))

    const persist = () => save.mutate(values)

    return (
        <div className="space-y-4">
            {ACCOUNT_PLATFORMS.map((platform) => (
                <div key={platform.key} className="rounded-lg border bg-card p-4">
                    <h3 className="mb-3 font-semibold">{platform.label}</h3>
                    <div className="grid grid-cols-1 gap-x-6 gap-y-4 md:grid-cols-3">
                        {platform.fields.map((field) => (
                            <div key={field.key} className="flex flex-col gap-1.5">
                                <Label className="text-sm">{t(field.labelKey)}</Label>
                                <FieldRenderer
                                    field={{ ...field, label: t(field.labelKey) }}
                                    value={
                                        (values[platform.key] ?? {})[
                                            field.key.replace(`${platform.key}_`, "")
                                        ]
                                    }
                                    onChange={(v) =>
                                        setField(
                                            platform.key,
                                            field.key.replace(`${platform.key}_`, ""),
                                            String(v ?? ""),
                                        )
                                    }
                                />
                            </div>
                        ))}
                    </div>
                </div>
            ))}
            <Button onClick={persist} disabled={save.isPending}>
                {save.isPending && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
                {t("common.save")}
            </Button>
        </div>
    )
}

function PoseSettings() {
    const { t } = useI18n()
    const form = useSettingsForm()

    if (form.isLoading || !form.data) {
        return (
            <div className="flex justify-center py-20">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
        )
    }

    const pose = (form.values.pose_detection ?? {}) as Record<string, unknown>

    const detectionFields: FieldDef[] = [
        { key: "frame_seconds", label: t("settings.frameSeconds"), type: "number" },
        { key: "imgsz", label: t("settings.imgsz"), type: "number" },
        { key: "batch_size", label: t("settings.batchSize"), type: "number" },
        { key: "inference_threads", label: t("settings.inferenceThreads"), type: "number", hint: t("settings.inferenceThreadsHint") },
        { key: "confidence_threshold", label: t("settings.confidence"), type: "number" },
        { key: "enable_pose_detection", label: t("settings.poseModel"), type: "switch" },
        {
            key: "pose_filter",
            label: t("settings.poseFilter"),
            type: "select",
            options: [
                { value: "none", label: t("settings.poseFilterNone") },
                { value: "standing", label: t("settings.poseFilterStanding") },
                { value: "sitting", label: t("settings.poseFilterSitting") },
            ],
        },
        { key: "person_min_ratio", label: t("settings.personMinRatio"), type: "number" },
    ]

    const segmentFields: FieldDef[] = [
        { key: "merge_threshold_seconds", label: t("settings.mergeThreshold"), type: "number" },
        { key: "min_segment_seconds", label: t("settings.minSegment"), type: "number" },
        { key: "merge_clips", label: t("settings.mergeClips"), type: "switch" },
        { key: "delete_original_video", label: t("settings.deleteOriginal"), type: "switch" },
        { key: "move_output_to_input", label: t("settings.moveToOriginal"), type: "switch" },
        { key: "video_output_dir", label: t("settings.outputDir"), type: "text" },
    ]

    const clipFilterFields: FieldDef[] = [
        { key: "clip_filter_enabled", label: t("settings.clipFilterEnabled"), type: "switch" },
        {
            key: "clip_min_positive_ratio",
            label: t("settings.clipMinPositiveRatio"),
            type: "number",
            hint: t("settings.clipMinPositiveRatioHint"),
        },
        {
            key: "clip_sample_seconds",
            label: t("settings.clipSampleSeconds"),
            type: "number",
            hint: t("settings.clipSampleSecondsHint"),
        },
        {
            key: "clip_save_reports",
            label: t("settings.clipSaveReports"),
            type: "switch",
            hint: t("settings.clipSaveReportsHint"),
        },
    ]

    return (
        <div className="space-y-4">
            <div className="rounded-lg border bg-card p-4">
                <div className="mb-3 flex items-center justify-between">
                    <div>
                        <h3 className="font-semibold">{t("settings.poseEnabledGlobal")}</h3>
                        <p className="text-xs text-muted-foreground">
                            {t("settings.poseEnabledGlobalDesc")}
                        </p>
                    </div>
                    <Switch
                        checked={Boolean(pose.enabled)}
                        onCheckedChange={(v) => form.setIn("pose_detection", "enabled", v)}
                    />
                </div>
            </div>

            <div className="rounded-lg border bg-card p-4">
                <h3 className="mb-3 font-semibold">{t("settings.poseGeneral")}</h3>
                <div className="grid grid-cols-1 gap-x-6 gap-y-4 md:grid-cols-2">
                    {detectionFields.map((field) => (
                        <FieldRow key={field.key} field={field} form={form} scope="pose_detection" />
                    ))}
                </div>
            </div>

            <div className="rounded-lg border bg-card p-4">
                <h3 className="mb-3 font-semibold">{t("settings.clipOptions")}</h3>
                <div className="grid grid-cols-1 gap-x-6 gap-y-4 md:grid-cols-2">
                    {segmentFields.map((field) => (
                        <FieldRow key={field.key} field={field} form={form} scope="pose_detection" />
                    ))}
                </div>
            </div>

            <div className="rounded-lg border bg-card p-4">
                <div className="mb-3">
                    <h3 className="font-semibold">{t("settings.clipClothingFilter")}</h3>
                    <p className="text-xs text-muted-foreground">
                        {t("settings.clipClothingFilterDesc")}
                    </p>
                </div>
                <div className="grid grid-cols-1 gap-x-6 gap-y-4 md:grid-cols-2">
                    {clipFilterFields.map((field) => (
                        <FieldRow key={field.key} field={field} form={form} scope="pose_detection" />
                    ))}
                </div>
            </div>
        </div>
    )
}

const UPLOAD_PAGE_SIZE = 15

function UploadSettings() {
    const { t, tf, lang } = useI18n()
    const form = useSettingsForm()
    const queryClient = useQueryClient()
    const [bilibiliLoginOpen, setBilibiliLoginOpen] = useState(false)
    const [page, setPage] = useState(1)
    const uploadsQueryKey = ["uploads", page] as const
    const tasks = useQuery({
        queryKey: uploadsQueryKey,
        queryFn: () => uploadsApi.list(page, UPLOAD_PAGE_SIZE),
        // 翻页时保留上一页数据：否则 total 会瞬间变 0、页码被下面的越界保护退回第 1 页
        placeholderData: keepPreviousData,
        refetchInterval: (query) =>
            query.state.data?.jobs.some((job) => ["pending", "uploading", "retrying"].includes(job.status))
                ? 3000
                : 15000,
    })
    const total = tasks.data?.total ?? 0
    const totalPages = Math.max(1, Math.ceil(total / UPLOAD_PAGE_SIZE))
    // 页码标签跟当前显示的这批数据对齐（翻页瞬间还在显示上一页）
    const shownPage = tasks.data?.page ?? page

    // 记录只留最近 100 条，翻到末页后再有新任务/清理，页码可能越界，退到末页；
    // 新页数据还没回来时 total 不可信，别在这里判
    useEffect(() => {
        if (tasks.data && page > totalPages) setPage(totalPages)
    }, [tasks.data, page, totalPages])
    const retry = useMutation({
        mutationFn: uploadsApi.retry,
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ["uploads"] }),
        onError: (e: Error) => toast.error(translateError(e.message)),
    })
    const pauseJob = useMutation({
        mutationFn: uploadsApi.pause,
        onSuccess: () => {
            toast.success(t("settings.uploadJobPausedToast"))
            queryClient.invalidateQueries({ queryKey: ["uploads"] })
        },
        onError: (e: Error) => toast.error(translateError(e.message)),
    })
    const resumeJob = useMutation({
        mutationFn: uploadsApi.resumeJob,
        onSuccess: () => {
            toast.success(t("settings.uploadJobResumedToast"))
            queryClient.invalidateQueries({ queryKey: ["uploads"] })
        },
        onError: (e: Error) => toast.error(translateError(e.message)),
    })
    const removeJob = useMutation({
        mutationFn: uploadsApi.removeJob,
        onSuccess: () => {
            toast.success(t("settings.uploadJobDeletedToast"))
            queryClient.invalidateQueries({ queryKey: ["uploads"] })
        },
        onError: (e: Error) => toast.error(translateError(e.message)),
    })
    const jobBusy = pauseJob.isPending || resumeJob.isPending || removeJob.isPending
    const resume = useMutation({
        mutationFn: uploadsApi.resumeChannel,
        onSuccess: () => {
            toast.success(t("settings.uploadChannelResumed"))
            queryClient.invalidateQueries({ queryKey: ["uploads"] })
        },
        onError: (e: Error) => toast.error(translateError(e.message)),
    })
    // 手动暂停整条渠道（与限流冷却不同：不会自己恢复，也不清节奏门）
    const pauseQueue = useMutation({
        mutationFn: uploadsApi.pauseChannel,
        onSuccess: () => {
            toast.success(t("settings.uploadQueuePausedToast"))
            queryClient.invalidateQueries({ queryKey: ["uploads"] })
        },
        onError: (e: Error) => toast.error(translateError(e.message)),
    })
    const resumeQueue = useMutation({
        mutationFn: uploadsApi.resumeChannel,
        onSuccess: () => {
            toast.success(t("settings.uploadQueueResumedToast"))
            queryClient.invalidateQueries({ queryKey: ["uploads"] })
        },
        onError: (e: Error) => toast.error(translateError(e.message)),
    })

    if (form.isLoading || !form.data) {
        return <div className="flex justify-center py-20"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
    }

    const uploads = (form.values.uploads ?? {}) as Rec
    const providers = (uploads.providers ?? {}) as Rec
    const bilibili = (providers.bilibili ?? {}) as Rec
    const bilibiliChannel = (tasks.data?.channels ?? []).find((c) => c.provider === "bilibili")
    const setUpload = (key: string, value: unknown) => form.set("uploads", { ...uploads, [key]: value })
    const setBilibili = (key: string, value: unknown) => form.set("uploads", {
        ...uploads,
        providers: { ...providers, bilibili: { ...bilibili, [key]: value } },
    })

    const input = (key: string, type = "text") => (
        <Input
            type={type}
            value={String(bilibili[key] ?? "")}
            onChange={(e) => setBilibili(key, type === "number" ? Number(e.target.value) : e.target.value)}
        />
    )

    return (
        <div className="space-y-4">
            <div className="rounded-lg border bg-card p-4">
                <div className="mb-4 flex items-center justify-between">
                    <div><h3 className="font-semibold">{t("settings.uploadEnabled")}</h3><p className="text-xs text-muted-foreground">{t("settings.uploadEnabledHint")}</p></div>
                    <Switch checked={Boolean(uploads.enabled)} onCheckedChange={(v) => setUpload("enabled", v)} />
                </div>
            </div>
            <div className="rounded-lg border bg-card p-4">
                <div className="mb-4">
                    <h3 className="font-semibold">{t("settings.uploadPacing")}</h3>
                    <p className="mt-1 text-xs text-muted-foreground">{t("settings.uploadPacingHint")}</p>
                </div>
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                    <div className="space-y-1.5">
                        <Label>{t("settings.uploadMinInterval")}</Label>
                        <Input
                            type="number"
                            min={1}
                            step={5}
                            value={String(Math.round(Number(uploads.min_interval_seconds ?? 1800) / 60))}
                            onChange={(e) => {
                                const minutes = Math.max(1, Math.round(Number(e.target.value) || 0))
                                setUpload("min_interval_seconds", minutes * 60)
                            }}
                        />
                        <p className="text-xs text-muted-foreground">{t("settings.uploadMinIntervalHint")}</p>
                    </div>
                    <div className="space-y-1.5">
                        <Label>{t("settings.uploadNextSlot")}</Label>
                        <p className="text-sm" data-testid="upload-next-slot">
                            {bilibiliChannel?.next_slot_at
                                ? formatDateTime(new Date(bilibiliChannel.next_slot_at * 1000).toISOString(), lang)
                                : t("settings.uploadNextSlotNow")}
                        </p>
                    </div>
                </div>
            </div>
            <div className="rounded-lg border bg-card p-4">
                <div className="mb-4 flex items-center justify-between">
                    <div><div className="flex items-center gap-3"><h3 className="font-semibold">Bilibili</h3><Button size="sm" variant="outline" onClick={() => setBilibiliLoginOpen(true)}><QrCode className="mr-1 h-4 w-4" />{t("following.scanLogin")}</Button></div><p className="mt-1 text-xs text-muted-foreground">{t("settings.bilibiliHint")}</p></div>
                    <Switch checked={Boolean(bilibili.enabled)} onCheckedChange={(v) => setBilibili("enabled", v)} />
                </div>
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                    <div className="space-y-1.5"><Label>{t("settings.bilibiliCookieFile")}</Label>{input("cookie_file")}</div>
                    <div className="space-y-1.5"><Label>{t("settings.bilibiliTid")}</Label>{input("tid", "number")}</div>
                    <div className="space-y-1.5"><Label>{t("settings.bilibiliTags")}</Label>{input("tags")}</div>
                    <div className="space-y-1.5"><Label>{t("settings.bilibiliDescription")}</Label>{input("description")}</div>
                    <div className="flex items-center justify-between rounded border p-3"><Label>{t("settings.bilibiliOnlySelf")}</Label><Switch checked={Boolean(bilibili.only_self)} onCheckedChange={(v) => setBilibili("only_self", v)} /></div>
                    <div className="flex items-center justify-between rounded border p-3"><Label>{t("settings.bilibiliNoReprint")}</Label><Switch checked={Boolean(bilibili.no_reprint)} onCheckedChange={(v) => setBilibili("no_reprint", v)} /></div>
                </div>
            </div>

            <BilibiliLoginDialog open={bilibiliLoginOpen} onOpenChange={setBilibiliLoginOpen} />
            <div className="rounded-lg border bg-card p-4">
                <div className="mb-3 flex items-center justify-between">
                    <h3 className="font-semibold">{t("settings.uploadTasks")}</h3>
                    <div className="flex items-center gap-1">
                        {(tasks.data?.channels ?? []).map((channel) => channel.status === "active" && (
                            <Button
                                key={channel.provider}
                                variant="outline"
                                size="sm"
                                onClick={() => pauseQueue.mutate(channel.provider)}
                                disabled={pauseQueue.isPending}
                                data-testid="upload-queue-pause"
                            >
                                <Pause className="mr-1 h-3 w-3" />
                                {t("settings.uploadQueuePause")}
                            </Button>
                        ))}
                        <Button variant="ghost" size="sm" onClick={() => tasks.refetch()} disabled={tasks.isFetching}>
                            <RefreshCw className={`mr-1 h-4 w-4 ${tasks.isFetching ? "animate-spin" : ""}`} />
                            {t("common.refresh")}
                        </Button>
                    </div>
                </div>
                {(tasks.data?.channels ?? []).map((channel) => channel.status === "paused" && (
                    <div key={channel.provider} className="mb-3 rounded border bg-muted/40 p-3" data-testid="upload-queue-paused">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                            <div>
                                <p className="font-medium">{t("settings.uploadQueuePaused")}</p>
                                <p className="mt-1 text-xs text-muted-foreground">{t("settings.uploadQueuePausedHint")}</p>
                            </div>
                            <Button size="sm" onClick={() => resumeQueue.mutate(channel.provider)} disabled={resumeQueue.isPending} data-testid="upload-queue-resume">{t("settings.uploadQueueResume")}</Button>
                        </div>
                    </div>
                ))}
                {(tasks.data?.channels ?? []).map((channel) => channel.status === "cooling" && (
                    <div key={channel.provider} className="mb-3 rounded border border-amber-500/40 bg-amber-500/5 p-3" data-testid="upload-rate-limited">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                            <div>
                                <p className="font-medium text-amber-600">{t("settings.uploadRateLimited")}</p>
                                <p className="mt-1 text-xs text-muted-foreground">{t("settings.uploadRateLimitedHint")}</p>
                                <p className="mt-1 text-xs text-muted-foreground" data-testid="upload-rate-limited-until">
                                    {tf("settings.uploadRateLimitedUntil", { time: formatDateTime(channel.resume_at ? new Date(channel.resume_at * 1000).toISOString() : null, lang) })}
                                </p>
                            </div>
                            <Button size="sm" onClick={() => resume.mutate(channel.provider)} disabled={resume.isPending}>{t("settings.uploadResume")}</Button>
                        </div>
                    </div>
                ))}
                {(tasks.data?.channels ?? []).map((channel) => channel.status === "blocked_auth" && (
                    <div key={channel.provider} className="mb-3 rounded border border-destructive/40 bg-destructive/5 p-3">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                            <div><p className="font-medium text-destructive">{t("settings.uploadLoginExpired")}</p><p className="mt-1 text-xs text-muted-foreground">{t("settings.uploadLoginExpiredHint")}</p></div>
                            <Button size="sm" onClick={() => resume.mutate(channel.provider)} disabled={resume.isPending}>{t("settings.uploadResume")}</Button>
                        </div>
                    </div>
                ))}
                {tasks.isLoading ? (
                    <div className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin" /></div>
                ) : (tasks.data?.jobs.length ?? 0) === 0 ? (
                    <p className="py-8 text-center text-sm text-muted-foreground">{t("settings.uploadTasksEmpty")}</p>
                ) : (
                    <div className="space-y-2" data-testid="upload-tasks">
                        {tasks.data?.jobs.map((job) => (
                            <UploadJobRow
                                key={job.id}
                                job={job}
                                busy={retry.isPending || jobBusy}
                                onRetry={() => retry.mutate(job.id)}
                                onPause={() => pauseJob.mutate(job.id)}
                                onResume={() => resumeJob.mutate(job.id)}
                                onDelete={() => removeJob.mutate(job.id)}
                                t={t}
                                tf={tf}
                                lang={lang}
                            />
                        ))}
                    </div>
                )}
                {total > 0 && (
                    <div
                        // 手机上底部固定导航会遮住页面最下面一截；这里留出间距，滚动到底后分页条稳稳露在导航上方
                        className="mb-2 mt-3 flex flex-wrap items-center justify-between gap-2 border-t pt-3"
                    >
                        <span className="text-xs text-muted-foreground" data-testid="upload-total">
                            {tf("settings.uploadTotal", { count: total })}
                        </span>
                        <div className="flex items-center gap-2">
                            <Button
                                variant="outline"
                                size="sm"
                                data-testid="upload-prev"
                                disabled={page <= 1 || tasks.isFetching}
                                onClick={() => setPage((p) => Math.max(1, p - 1))}
                            >
                                {t("settings.uploadPrevPage")}
                            </Button>
                            <span className="whitespace-nowrap text-xs text-muted-foreground" data-testid="upload-page">
                                {tf("settings.uploadPageOf", { page: shownPage, max: totalPages })}
                            </span>
                            <Button
                                variant="outline"
                                size="sm"
                                data-testid="upload-next"
                                disabled={page >= totalPages || tasks.isFetching}
                                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                            >
                                {t("settings.uploadNextPage")}
                            </Button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    )
}

function UploadJobRow({ job, busy, onRetry, onPause, onResume, onDelete, t, tf, lang }: {
    job: UploadJob
    busy: boolean
    onRetry: () => void
    onPause: () => void
    onResume: () => void
    onDelete: () => void
    t: (key: string) => string
    tf: (key: string, params: Record<string, string | number>) => string
    lang: LangCode
}) {
    const confirmBox = useConfirm()
    // 合集（多P）稿件在列表里只看到「5 文件」，要能展开确认真投了哪几个
    const [expanded, setExpanded] = useState(false)
    const success = job.status === "completed"
    const failed = job.status === "failed" || job.status === "blocked_auth"
    const finished = success || failed
    const paused = job.status === "paused"
    const uploading = job.status === "uploading"
    const canPause = job.status === "pending" || job.status === "retrying"
    const label = t(`settings.uploadStatus${job.status.replace(/(^|_)(\w)/g, (_, _p, c) => c.toUpperCase())}`)
    const handleDelete = async () => {
        const ok = await confirmBox({
            description: tf("settings.uploadDeleteConfirm", { title: job.title }),
            confirmText: t("settings.uploadDelete"),
            destructive: true,
        })
        if (ok) onDelete()
    }
    return (
        <div className="rounded border p-3 text-sm" data-testid="upload-job" data-status={job.status}>
            {/* 手机上标题/按钮竖排，别让标题被按钮挤成省略号 */}
            <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
                <div className="min-w-0 flex-1">
                    <div className="flex items-start gap-2">
                        {success ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-green-600" /> : failed ? <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" /> : paused ? <Pause className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" /> : <Loader2 className={`mt-0.5 h-4 w-4 shrink-0 ${uploading ? "animate-spin" : ""}`} />}
                        <span className="min-w-0 flex-1 break-all font-medium leading-snug line-clamp-2" title={job.title}>{job.title}</span>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
                        {job.files.length > 0 ? (
                            <button
                                type="button"
                                className="inline-flex items-center gap-0.5 rounded hover:text-foreground"
                                onClick={() => setExpanded((v) => !v)}
                                data-testid="upload-files-toggle"
                                aria-expanded={expanded}
                            >
                                <ChevronDown className={`h-3 w-3 transition-transform ${expanded ? "rotate-180" : ""}`} />
                                {job.files.length > 1
                                    ? tf("settings.uploadParts", { count: job.files.length })
                                    : `${job.files.length} ${t("settings.uploadFiles")}`}
                            </button>
                        ) : (
                            <span>{job.files.length} {t("settings.uploadFiles")}</span>
                        )}
                        <span>{t("settings.uploadAttempts")} {job.attempts}</span>
                        <span>{t("settings.uploadCreatedAt")} {formatDateTime(job.created_at, lang)}</span>
                        {finished && <span>{t("settings.uploadFinishedAt")} {formatDateTime(job.updated_at, lang)}</span>}
                    </div>
                    {expanded && job.files.length > 0 && (
                        <ol className="mt-1 space-y-0.5 rounded bg-muted/40 p-2 text-xs" data-testid="upload-files-list">
                            {job.files.map((file, index) => (
                                <li key={`${index}-${file}`} className="flex gap-1.5">
                                    <span className="shrink-0 tabular-nums text-muted-foreground">P{index + 1}</span>
                                    <span className="min-w-0 break-all" title={file}>{file.split("/").pop() || file}</span>
                                </li>
                            ))}
                        </ol>
                    )}
                    {job.message && failed && <p className="mt-1 line-clamp-2 break-all text-xs text-destructive" title={job.message}>{job.message}</p>}
                </div>
                {/* 徽章跟按钮放同一行并 items-center：之前徽章在标题行 top 对齐，比 32px 高的按钮高出一截 */}
                <div className="flex shrink-0 flex-wrap items-center gap-1">
                    <span className="mr-0.5 shrink-0 rounded bg-muted px-1.5 py-0.5 text-xs leading-5" data-testid="upload-job-status">{label}</span>
                    {job.remote_id && <Button asChild variant="outline" size="sm"><a href={`https://www.bilibili.com/video/${job.remote_id}`} target="_blank" rel="noreferrer">{job.remote_id}<ExternalLink className="ml-1 h-3 w-3" /></a></Button>}
                    {failed && <Button variant="outline" size="sm" onClick={onRetry} disabled={busy || job.status === "blocked_auth"}>{t("settings.uploadRetry")}</Button>}
                    {canPause && <Button variant="outline" size="sm" onClick={onPause} disabled={busy} data-testid="upload-job-pause"><Pause className="mr-1 h-3 w-3" />{t("settings.uploadJobPause")}</Button>}
                    {paused && <Button variant="outline" size="sm" onClick={onResume} disabled={busy} data-testid="upload-job-resume"><Play className="mr-1 h-3 w-3" />{t("settings.uploadJobResume")}</Button>}
                    {/* 正在上传的不给删（后端也拒），避免出现「界面没了但进程还在跑」 */}
                    {!uploading && <Button variant="outline" size="sm" onClick={handleDelete} disabled={busy} data-testid="upload-job-delete"><Trash2 className="mr-1 h-3 w-3" />{t("settings.uploadDelete")}</Button>}
                </div>
            </div>
        </div>
    )
}

/** 配置驱动的设置字段渲染器 */
type FieldType = "switch" | "text" | "number" | "select" | "checkbox"

interface FieldDef {
    key: string
    label: string
    type: FieldType
    options?: Array<{ value: string; label: string }>
    placeholder?: string
    hint?: string
}

interface SectionDef {
    title: string
    fields: FieldDef[]
}

type Rec = Record<string, unknown>

function isPlainObject(value: unknown): value is Rec {
    return typeof value === "object" && value !== null && !Array.isArray(value)
}

/** 递归挑出与基线不同的键，作为 PUT 的 patch */
function diffValues(next: Rec, base: Rec): Rec {
    const patch: Rec = {}
    for (const [key, value] of Object.entries(next)) {
        const previous = base[key]
        if (isPlainObject(value) && isPlainObject(previous)) {
            const sub = diffValues(value, previous)
            if (Object.keys(sub).length > 0) patch[key] = sub
        } else if (JSON.stringify(value) !== JSON.stringify(previous)) {
            patch[key] = value
        }
    }
    return patch
}

/** 按点号路径写入一个值，返回新对象（沿途各层都复制，不改原对象） */
function setPath(source: Rec, path: string, value: unknown): Rec {
    const [head, ...rest] = path.split(".")
    if (rest.length === 0) return { ...source, [head]: value }
    const section = isPlainObject(source[head]) ? (source[head] as Rec) : {}
    return { ...source, [head]: setPath(section, rest.join("."), value) }
}

interface LayerInfo {
    /** 默认层里的值，用于「恢复默认」提示 */
    defaultValue: unknown
    /** 该键在用户覆盖层里有值（= 已偏离默认，而不是继承默认） */
    overridden: boolean
}

/**
 * 设置表单的统一装配：加载有效配置、只发变更键、覆盖态查询与恢复默认。
 *
 * 只发「用户真正编辑过的键」是这里的要点：把整份合并视图写回去会把默认层
 * 物化进用户覆盖层，之后镜像里改默认值对这台机器就永久失效了，新增的默认键
 * 还会在用户下一次保存任意设置时被固化。
 */
function useSettingsForm(delay = 1200) {
    const { t } = useI18n()
    const queryClient = useQueryClient()
    const { data, isLoading } = useQuery({ queryKey: ["settings"], queryFn: settingsApi.get })

    const [values, setValues] = useState<Rec>({})
    // 服务器已知状态：diff 的基线。null = 首屏数据还没到，此时不许发保存
    const baseline = useRef<Rec | null>(null)

    useEffect(() => {
        if (!data) return
        setValues(data.user_settings)
        baseline.current = data.user_settings
    }, [data?.user_settings]) // eslint-disable-line react-hooks/exhaustive-deps

    /** 写成功后就地更新缓存里的覆盖层与指纹：不 refetch，避免把在途编辑冲掉 */
    const absorb = (result: { version: string; user_overrides: Rec }) => {
        queryClient.setQueryData<SettingsData>(["settings"], (old) =>
            old ? { ...old, version: result.version, user_overrides: result.user_overrides } : old,
        )
    }

    const onWriteError = (e: Error) => {
        const code = e.message.split("|")[0]
        if (code === "err.settingsVersionMismatch" || code === "err.settingsStaleClient") {
            // 配置被别处改过 / 页面 JS 是更新前的旧版：拉服务器最新状态重置表单
            toast.error(translateError(e.message))
            queryClient.invalidateQueries({ queryKey: ["settings"] })
            return
        }
        toast.error(t("settings.saveFailed"))
    }

    const currentVersion = () => queryClient.getQueryData<SettingsData>(["settings"])?.version

    const save = useMutation({
        mutationFn: ({ patch }: { patch: Rec; sent: Rec }) =>
            settingsApi.update(patch, currentVersion()),
        onSuccess: (result: SettingsWriteResult, { sent }) => {
            baseline.current = sent
            absorb(result)
            // 改了「最小间隔」这类键会立刻改变下一单时刻，让上传面板重新取一次渠道数据
            queryClient.invalidateQueries({ queryKey: ["uploads"] })
            toast.success(t("settings.saved"))
        },
        onError: onWriteError,
    })

    const reset = useMutation({
        mutationFn: (path: string) => settingsApi.resetKey(path, currentVersion()),
        onSuccess: (result: SettingsResetResult, path) => {
            absorb(result)
            // 表单同步成默认值，基线一起挪过去——否则下一次 diff 会把它当成新的
            // 显式覆盖再写回服务器
            setValues((v) => {
                const next = setPath(v, path, result.value)
                baseline.current = next
                return next
            })
            toast.success(t("settings.resetDone"))
        },
        onError: onWriteError,
    })

    useEffect(() => {
        if (!data || baseline.current === null) return
        if (baseline.current === values) return // 加载后未编辑（同一对象引用）
        const patch = diffValues(values, baseline.current)
        if (Object.keys(patch).length === 0) return
        const sent = values
        const timer = setTimeout(() => save.mutate({ patch, sent }), delay)
        return () => clearTimeout(timer)
    }, [values, data, delay]) // eslint-disable-line react-hooks/exhaustive-deps

    return {
        data,
        isLoading,
        values,
        saving: save.isPending,
        resetting: reset.isPending,
        set: (key: string, value: unknown) => setValues((v) => ({ ...v, [key]: value })),
        setIn: (scope: string, key: string, value: unknown) =>
            setValues((v) => ({ ...v, [scope]: { ...((v[scope] ?? {}) as Rec), [key]: value } })),
        resetKey: (path: string) => reset.mutate(path),
        /** 某个键的分层信息；scope 用于 pose_detection 这类嵌套段 */
        layer: (key: string, scope?: string): LayerInfo => {
            const defaults = (data?.default_settings ?? {}) as Rec
            const overrides = (data?.user_overrides ?? {}) as Rec
            if (scope) {
                const defaultSection = (defaults[scope] ?? {}) as Rec
                const overrideSection = (overrides[scope] ?? {}) as Rec
                return { defaultValue: defaultSection[key], overridden: key in overrideSection }
            }
            return { defaultValue: defaults[key], overridden: key in overrides }
        },
    }
}

function RecordingSettings() {
    const { t } = useI18n()
    const form = useSettingsForm()

    if (form.isLoading || !form.data) {
        return (
            <div className="flex justify-center py-20">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
        )
    }

    const sections: SectionDef[] = [
        {
            title: t("settings.recording"),
            fields: [
                { key: "language", label: t("settings.language"), type: "select", options: [
                    { value: "Chinese", label: "简体中文" },
                    { value: "English", label: "English" },
                ]},
                { key: "filename_includes_title", label: t("settings.filenameIncludeTitle"), type: "switch" },
                { key: "custom_filename_template", label: t("settings.customFilenameTemplate"), type: "text" },
                { key: "live_save_path", label: t("settings.savePath"), type: "text", hint: t("settings.savePathHint") },
                { key: "remove_emojis", label: t("settings.removeEmojis"), type: "switch" },
            ],
        },
        {
            title: t("settings.proxy"),
            fields: [
                { key: "enable_proxy", label: t("settings.enableProxy"), type: "switch" },
                { key: "proxy_address", label: t("settings.proxyAddress"), type: "text" },
                { key: "default_platform_with_proxy", label: t("settings.proxyPlatforms"), type: "text" },
            ],
        },
        {
            title: t("settings.recordOptions"),
            fields: [
                { key: "video_format", label: t("settings.videoFormat"), type: "select", options: ["TS", "FLV", "HLS", "MP4"].map(f => ({ value: f, label: f })) },
                { key: "record_quality", label: t("settings.recordQuality"), type: "select", options: ["OD", "UHD", "HD", "SD", "LD"].map(q => ({ value: q, label: t(`quality.${q}`) })) },
                { key: "loop_time_seconds", label: t("settings.loopTime"), type: "number" },
                { key: "segmented_recording_enabled", label: t("settings.segmented"), type: "switch" },
                { key: "video_segment_time", label: t("settings.segmentTime"), type: "number" },
                { key: "force_https_recording", label: t("settings.forceHttps"), type: "switch" },
                { key: "default_live_source", label: t("settings.defaultLiveSource"), type: "select", options: [
                    { value: "FLV", label: "FLV" },
                    { value: "HLS", label: "HLS" },
                ]},
                { key: "flv_use_direct_download", label: t("settings.flvDirect"), type: "switch" },
                { key: "recording_space_threshold", label: t("settings.spaceThreshold"), type: "number" },
                { key: "convert_to_mp4", label: t("settings.convertMp4"), type: "switch" },
                { key: "delete_original", label: t("settings.deleteOriginal"), type: "switch" },
                { key: "generate_time_subtitle_file", label: t("settings.timeSubtitle"), type: "switch" },
                { key: "execute_custom_script", label: t("settings.customScript"), type: "switch" },
                { key: "custom_script_command", label: t("settings.customScriptCommand"), type: "text" },
                { key: "platform_max_concurrent_requests", label: t("settings.concurrent"), type: "number" },
                { key: "check_live_on_browser_refresh", label: t("settings.checkOnRefresh"), type: "switch" },
            ],
        },
        {
            title: t("settings.monitorStrategy"),
            fields: [
                { key: "monitor_jitter_ratio", label: t("settings.monitorJitter"), type: "number", hint: t("settings.monitorJitterHint") },
                { key: "monitor_failure_backoff_enabled", label: t("settings.monitorBackoff"), type: "switch", hint: t("settings.monitorBackoffHint") },
                { key: "monitor_failure_backoff_max_multiplier", label: t("settings.monitorBackoffMax"), type: "number" },
                { key: "monitor_global_error_delay_seconds", label: t("settings.monitorGlobalDelay"), type: "number" },
                { key: "monitor_global_error_threshold", label: t("settings.monitorGlobalThreshold"), type: "number" },
                { key: "monitor_dynamic_concurrency_enabled", label: t("settings.monitorDynamicConc"), type: "switch", hint: t("settings.monitorDynamicConcHint") },
                { key: "monitor_post_record_recheck_seconds", label: t("settings.monitorRecheck"), type: "number", hint: t("settings.monitorRecheckHint") },
                { key: "monitor_unsupported_failure_limit", label: t("settings.monitorUnsupportedLimit"), type: "number", hint: t("settings.monitorUnsupportedLimitHint") },
                { key: "auto_stop_monitor_days", label: t("settings.autoStopMonitorDays"), type: "number", hint: t("settings.autoStopMonitorDaysHint") },
                { key: "monitor_platform_min_interval_seconds", label: t("settings.monitorMinInterval"), type: "number", hint: t("settings.monitorMinIntervalHint") },
                { key: "monitor_recency_priority_enabled", label: t("settings.monitorRecencyPriority"), type: "switch", hint: t("settings.monitorRecencyPriorityHint") },
            ],
        },
    ]

    return (
        <div className="space-y-4">
            {sections.map((section) => (
                <div key={section.title} className="rounded-lg border bg-card p-4">
                    <h3 className="mb-3 font-semibold">{section.title}</h3>
                    <div className="grid grid-cols-1 gap-x-6 gap-y-4 md:grid-cols-2">
                        {section.fields.map((field) => (
                            <FieldRow key={field.key} field={field} form={form} />
                        ))}
                    </div>
                </div>
            ))}
        </div>
    )
}

/**
 * 一个设置字段：标签 + 覆盖态徽标（可一键恢复默认）+ 控件 + 提示。
 *
 * 徽标只在该键存在于用户覆盖层时出现——没有徽标就是「跟随默认层」，
 * 镜像里改默认值会作用到它。
 */
function FieldRow({
    field,
    form,
    scope,
}: {
    field: FieldDef
    form: ReturnType<typeof useSettingsForm>
    scope?: string
}) {
    const { t, tf } = useI18n()
    const section = scope ? ((form.values[scope] ?? {}) as Rec) : form.values
    const { defaultValue, overridden } = form.layer(field.key, scope)
    const path = scope ? `${scope}.${field.key}` : field.key

    return (
        <div className="flex flex-col gap-1.5">
            <div className="flex min-h-5 items-center gap-1.5">
                <Label className="text-sm">{field.label}</Label>
                {overridden && (
                    <button
                        type="button"
                        disabled={form.resetting}
                        onClick={() => form.resetKey(path)}
                        title={tf("settings.resetHint", { value: formatValue(defaultValue, t) })}
                        className="inline-flex items-center gap-0.5 rounded border px-1 py-0.5 text-[10px] leading-none text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
                    >
                        <RotateCcw className="h-2.5 w-2.5" />
                        {t("settings.overridden")}
                    </button>
                )}
            </div>
            <FieldRenderer
                field={field}
                value={section[field.key]}
                onChange={(v) =>
                    scope ? form.setIn(scope, field.key, v) : form.set(field.key, v)
                }
            />
            {field.hint && <span className="text-xs text-muted-foreground">{field.hint}</span>}
        </div>
    )
}

/** 默认值的可读形式（用于恢复默认的提示） */
function formatValue(value: unknown, t: (path: string) => string): string {
    if (typeof value === "boolean") return value ? t("common.yes") : t("common.no")
    if (value === "" || value == null) return t("settings.emptyValue")
    if (isPlainObject(value) || Array.isArray(value)) return JSON.stringify(value)
    return String(value)
}

function FieldRenderer({
    field,
    value,
    onChange,
}: {
    field: FieldDef
    value: unknown
    onChange: (value: unknown) => void
}) {
    switch (field.type) {
        case "switch":
            return (
                <div className="flex items-center gap-2 pt-1">
                    <Switch checked={Boolean(value)} onCheckedChange={(v) => onChange(v)} />
                </div>
            )
        case "checkbox":
            return (
                <Checkbox checked={Boolean(value)} onCheckedChange={(v) => onChange(Boolean(v))} />
            )
        case "select":
            return (
                <Select value={String(value ?? "")} onValueChange={(v) => onChange(v)}>
                    <SelectTrigger>
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        {(field.options ?? []).map((o) => (
                            <SelectItem key={o.value} value={o.value}>
                                {o.label}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            )
        case "number":
            return (
                <Input
                    type="number"
                    value={value == null ? "" : String(value)}
                    onChange={(e) => onChange(e.target.value)}
                />
            )
        default:
            return (
                <Input
                    value={value == null ? "" : String(value)}
                    placeholder={field.placeholder}
                    onChange={(e) => onChange(e.target.value)}
                />
            )
    }
}

const PLATFORM_KEYS = [
    "douyin", "tiktok", "kuaishou", "huya", "douyu", "yy", "bilibili", "xhs", "bigo",
    "blued", "soop", "netease", "qiandurebo", "pandalive", "maoerfm", "winktv",
    "flextv", "look", "popkontv", "twitcasting", "baidu", "weibo", "kugou", "twitch",
    "liveme", "huajiao", "liuxing", "showroom", "acfun", "changliao", "yinke",
    "yinbo", "zhihu", "chzzk", "haixiu", "vvxq", "17live", "lang", "piaopiao",
    "6room", "lehai", "catshow", "shopee", "youtube", "taobao", "jd",
]

/** Cookie 寿命文案 + 颜色：读不出 sid_guard（unknown）也要说一句，别让人以为能提醒。 */
function cookieExpiryLine(
    status: DouyinCookieStatus | undefined,
    lang: LangCode,
    t: (path: string) => string,
    tf: (path: string, params: Record<string, string | number>) => string,
): { text: string; className: string } | null {
    if (!status || status.state === "missing") return null
    if (status.state === "unknown") {
        return { text: t("settings.cookieExpiryUnknown"), className: "text-muted-foreground" }
    }
    const date = formatDate(status.expires_at, lang)
    if (status.state === "expired") {
        return { text: tf("settings.cookieExpired", { date }), className: "text-destructive" }
    }
    const days = status.days_left ?? 0
    if (status.state === "expiring") {
        return {
            text: tf("settings.cookieExpiring", { date, days: Math.max(1, Math.ceil(days)) }),
            className: "text-amber-600 dark:text-amber-500",
        }
    }
    return {
        text: tf("settings.cookieExpiresAt", { date, days: Math.floor(days) }),
        className: "text-muted-foreground",
    }
}

function CookiesTab() {
    const { t, tf, lang } = useI18n()
    const queryClient = useQueryClient()
    const { data, isLoading } = useQuery({
        queryKey: ["cookies"],
        queryFn: settingsApi.getCookies,
    })
    const [values, setValues] = useState<Record<string, string>>({})
    const [douyinLoginOpen, setDouyinLoginOpen] = useState(false)
    const [bilibiliLoginOpen, setBilibiliLoginOpen] = useState(false)

    // Cookie 寿命：后端只解析本地 sid_guard，不打抖音接口（没登录态时也能提醒）
    const cookieStatus = useQuery({
        queryKey: ["douyin-cookie"],
        queryFn: douyinApi.cookieStatus,
        staleTime: 60_000,
    })

    useEffect(() => {
        if (data) setValues(data.cookies)
    }, [data])

    const expiry = cookieExpiryLine(cookieStatus.data, lang, t, tf)

    if (isLoading) {
        return (
            <div className="flex justify-center py-20">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
        )
    }

    return (
        <div className="rounded-lg border bg-card p-4">
            <div className="mb-3"><h3 className="font-semibold">{t("settings.cookies")}</h3></div>
            <div className="grid grid-cols-1 gap-x-6 gap-y-4 lg:grid-cols-2">
                {PLATFORM_KEYS.map((key) => (
                    <div key={key} className="flex flex-col gap-1.5">
                        <div className="flex min-h-8 items-center justify-between gap-2">
                            <Label className="text-sm font-mono">{key}</Label>
                            {key === "douyin" && <Button size="sm" variant="outline" data-testid="douyin-scan-login" onClick={() => setDouyinLoginOpen(true)}><QrCode className="mr-1 h-4 w-4" />{t("following.scanLogin")}</Button>}
                            {key === "bilibili" && <Button size="sm" variant="outline" data-testid="bilibili-scan-login" onClick={() => setBilibiliLoginOpen(true)}><QrCode className="mr-1 h-4 w-4" />{t("following.scanLogin")}</Button>}
                        </div>
                        <Input
                            value={values[key] ?? ""}
                            placeholder={tf("settings.cookiesPlaceholder", { platform: key })}
                            onChange={(e) => setValues((v) => ({ ...v, [key]: e.target.value }))}
                            onBlur={() => {
                                void fetch("/api/settings/cookies", {
                                    method: "PUT",
                                    headers: { "Content-Type": "application/json" },
                                    body: JSON.stringify({ cookies: values }),
                                })
                                // 存完重算寿命提示（后端同时会做一次备份）
                                if (key === "douyin") {
                                    void queryClient.invalidateQueries({ queryKey: ["douyin-cookie"] })
                                }
                            }}
                        />
                        {key === "douyin" && expiry && (
                            <p
                                data-testid="settings-cookie-expiry"
                                className={`text-xs ${expiry.className}`}
                            >
                                {expiry.text}
                            </p>
                        )}
                    </div>
                ))}
            </div>
            <p className="mt-3 text-xs text-muted-foreground">{t("settings.cookieBackupHint")}</p>

            <DouyinLoginDialog
                open={douyinLoginOpen}
                onOpenChange={setDouyinLoginOpen}
                // 登录成功后端已写盘：拉一次就把输入框里的 douyin Cookie 同步成新的
                onSuccess={() => {
                    void queryClient.invalidateQueries({ queryKey: ["cookies"] })
                }}
            />
            <BilibiliLoginDialog open={bilibiliLoginOpen} onOpenChange={setBilibiliLoginOpen} />
        </div>
    )
}

function SecurityTab() {
    const { t } = useI18n()
    const [oldPw, setOldPw] = useState("")
    const [newPw, setNewPw] = useState("")
    const [confirmPw, setConfirmPw] = useState("")
    const [busy, setBusy] = useState(false)

    const changePw = async () => {
        if (newPw !== confirmPw) {
            toast.error(t("settings.passwordMismatch"))
            return
        }
        setBusy(true)
        try {
            const response = await fetch("/api/auth/password", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ old_password: oldPw, new_password: newPw }),
            })
            if (response.ok) {
                toast.success(t("settings.passwordChanged"))
                setOldPw("")
                setNewPw("")
                setConfirmPw("")
            } else {
                toast.error(t("settings.passwordWrong"))
            }
        } finally {
            setBusy(false)
        }
    }

    return (
        <div className="max-w-md space-y-4 rounded-lg border bg-card p-4">
            <h3 className="font-semibold">{t("settings.security")}</h3>
            <div className="space-y-1.5">
                <Label>{t("settings.oldPassword")}</Label>
                <Input type="password" value={oldPw} onChange={(e) => setOldPw(e.target.value)} />
            </div>
            <div className="space-y-1.5">
                <Label>{t("settings.newPassword")}</Label>
                <Input type="password" value={newPw} onChange={(e) => setNewPw(e.target.value)} />
            </div>
            <div className="space-y-1.5">
                <Label>{t("settings.confirmPassword")}</Label>
                <Input type="password" value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)} />
            </div>
            <Button onClick={changePw} disabled={busy || !oldPw || !newPw}>
                {t("settings.changePassword")}
            </Button>
        </div>
    )
}
