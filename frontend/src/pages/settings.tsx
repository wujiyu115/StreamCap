import { useQuery } from "@tanstack/react-query"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
import { useEffect, useState } from "react"
import { settingsApi } from "@/api"
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
import { useI18n } from "@/i18n"
import { toast } from "sonner"

export default function SettingsPage() {
    return (
        <Tabs defaultValue="recording" className="space-y-4">
            <TabsList>
                <TabsTrigger value="recording">录制设置</TabsTrigger>
                <TabsTrigger value="pose">人体识别</TabsTrigger>
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
    const { data, isLoading } = useQuery({ queryKey: ["settings"], queryFn: settingsApi.get })

    const [values, setValues] = useState<Record<string, unknown>>({})
    const ready = !!data
    const debounced = useDebouncedSave(values, ready)

    useEffect(() => {
        if (data) {
            setValues(data.user_settings)
            setTimeout(() => debounced.markReady(), 100)
        }
    }, [data]) // eslint-disable-line react-hooks/exhaustive-deps

    if (isLoading || !data) {
        return (
            <div className="flex justify-center py-20">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
        )
    }

    const set = (key: string, value: unknown) =>
        setValues((v) => ({ ...v, [key]: value }))

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
                        <div key={field.key} className="flex flex-col gap-1.5">
                            <Label className="text-sm">{field.label}</Label>
                            <FieldRenderer
                                field={field}
                                value={values[field.key]}
                                onChange={(v) => set(field.key, v)}
                            />
                        </div>
                    ))}
                </div>
            </div>

            {PUSH_CHANNELS.map((channel) => (
                <div key={channel.key} className="rounded-lg border bg-card p-4">
                    <div className="mb-3 flex items-center justify-between">
                        <h3 className="font-semibold">{t(channel.labelKey)}</h3>
                        <Switch
                            checked={Boolean(values[channel.enabledKey])}
                            onCheckedChange={(v) => set(channel.enabledKey, v)}
                        />
                    </div>
                    <div className="grid grid-cols-1 gap-x-6 gap-y-4 md:grid-cols-2">
                        {channel.fields.map((field) => (
                            <div key={field.key} className="flex flex-col gap-1.5">
                                <Label className="text-sm">{t(field.labelKey)}</Label>
                                <FieldRenderer
                                    field={{ ...field, label: t(field.labelKey) }}
                                    value={values[field.key]}
                                    onChange={(v) => set(field.key, v)}
                                />
                            </div>
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
    const { data, isLoading } = useQuery({ queryKey: ["settings"], queryFn: settingsApi.get })

    const [values, setValues] = useState<Record<string, unknown>>({})
    const ready = !!data
    const debounced = useDebouncedSave(values, ready)

    useEffect(() => {
        if (data) {
            setValues(data.user_settings)
            setTimeout(() => debounced.markReady(), 100)
        }
    }, [data]) // eslint-disable-line react-hooks/exhaustive-deps

    if (isLoading || !data) {
        return (
            <div className="flex justify-center py-20">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
        )
    }

    const pose = (values.pose_detection ?? {}) as Record<string, unknown>

    const setPose = (key: string, value: unknown) =>
        setValues((v) => ({
            ...v,
            pose_detection: { ...((v.pose_detection ?? {}) as Record<string, unknown>), [key]: value },
        }))

    const detectionFields: FieldDef[] = [
        { key: "frame_seconds", label: t("settings.frameSeconds"), type: "number" },
        { key: "imgsz", label: t("settings.imgsz"), type: "number" },
        { key: "batch_size", label: t("settings.batchSize"), type: "number" },
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
        { key: "video_output_dir", label: t("settings.outputDir"), type: "text" },
        { key: "min_file_age_minutes", label: t("settings.minFileAge"), type: "number" },
        { key: "wait_file_timeout_minutes", label: t("settings.waitFileTimeout"), type: "number" },
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
                        onCheckedChange={(v) => setPose("enabled", v)}
                    />
                </div>
            </div>

            <div className="rounded-lg border bg-card p-4">
                <h3 className="mb-3 font-semibold">{t("settings.poseGeneral")}</h3>
                <div className="grid grid-cols-1 gap-x-6 gap-y-4 md:grid-cols-2">
                    {detectionFields.map((field) => (
                        <div key={field.key} className="flex flex-col gap-1.5">
                            <Label className="text-sm">{field.label}</Label>
                            <FieldRenderer
                                field={field}
                                value={pose[field.key]}
                                onChange={(v) => setPose(field.key, v)}
                            />
                        </div>
                    ))}
                </div>
            </div>

            <div className="rounded-lg border bg-card p-4">
                <h3 className="mb-3 font-semibold">{t("settings.clipOptions")}</h3>
                <div className="grid grid-cols-1 gap-x-6 gap-y-4 md:grid-cols-2">
                    {segmentFields.map((field) => (
                        <div key={field.key} className="flex flex-col gap-1.5">
                            <Label className="text-sm">{field.label}</Label>
                            <FieldRenderer
                                field={field}
                                value={pose[field.key]}
                                onChange={(v) => setPose(field.key, v)}
                            />
                        </div>
                    ))}
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

function useSettingsMutation() {
    const { t } = useI18n()
    const queryClient = useQueryClient()
    return useMutation({
        mutationFn: (userSettings: Record<string, unknown>) => settingsApi.update(userSettings),
        onSuccess: () => {
            toast.success(t("settings.saved"))
            queryClient.invalidateQueries({ queryKey: ["settings"] })
        },
        onError: () => toast.error(t("settings.saveFailed")),
    })
}

function useDebouncedSave(
    values: Record<string, unknown>,
    ready: boolean,
    delay = 1200,
) {
    const save = useSettingsMutation()
    const [initial, setInitial] = useState(true)

    useEffect(() => {
        if (!ready || initial) return
        const timer = setTimeout(() => save.mutate(values), delay)
        return () => clearTimeout(timer)
    }, [values, ready, initial, delay]) // eslint-disable-line react-hooks/exhaustive-deps

    return {
        markReady: () => setInitial(false),
        saving: save.isPending,
        saveNow: () => save.mutate(values),
    }
}

function RecordingSettings() {
    const { t } = useI18n()
    const { data, isLoading } = useQuery({ queryKey: ["settings"], queryFn: settingsApi.get })

    const [values, setValues] = useState<Record<string, unknown>>({})
    const ready = !!data
    const debounced = useDebouncedSave(values, ready)

    useEffect(() => {
        if (data) {
            setValues(data.user_settings)
            setTimeout(() => debounced.markReady(), 100)
        }
    }, [data]) // eslint-disable-line react-hooks/exhaustive-deps

    if (isLoading || !data) {
        return (
            <div className="flex justify-center py-20">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
        )
    }

    const set = (key: string, value: unknown) =>
        setValues((v) => ({ ...v, [key]: value }))

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
    ]

    return (
        <div className="space-y-4">
            {sections.map((section) => (
                <div key={section.title} className="rounded-lg border bg-card p-4">
                    <h3 className="mb-3 font-semibold">{section.title}</h3>
                    <div className="grid grid-cols-1 gap-x-6 gap-y-4 md:grid-cols-2">
                        {section.fields.map((field) => (
                            <div key={field.key} className="flex flex-col gap-1.5">
                                <Label className="text-sm">{field.label}</Label>
                                <FieldRenderer
                                    field={field}
                                    value={values[field.key]}
                                    onChange={(v) => set(field.key, v)}
                                />
                                {field.hint && (
                                    <span className="text-xs text-muted-foreground">{field.hint}</span>
                                )}
                            </div>
                        ))}
                    </div>
                </div>
            ))}
        </div>
    )
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

function CookiesTab() {
    const { t, tf } = useI18n()
    const { data, isLoading } = useQuery({
        queryKey: ["cookies"],
        queryFn: settingsApi.getCookies,
    })
    const [values, setValues] = useState<Record<string, string>>({})
    const save = useSettingsMutation()

    useEffect(() => {
        if (data) setValues(data.cookies)
    }, [data])

    if (isLoading) {
        return (
            <div className="flex justify-center py-20">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
        )
    }

    return (
        <div className="rounded-lg border bg-card p-4">
            <div className="mb-3 flex items-center justify-between">
                <h3 className="font-semibold">{t("settings.cookies")}</h3>
            </div>
            <div className="grid grid-cols-1 gap-x-6 gap-y-4 lg:grid-cols-2">
                {PLATFORM_KEYS.map((key) => (
                    <div key={key} className="flex flex-col gap-1.5">
                        <Label className="text-sm font-mono">{key}</Label>
                        <Input
                            value={values[key] ?? ""}
                            placeholder={tf("settings.cookiesPlaceholder", { platform: key })}
                            onChange={(e) => setValues((v) => ({ ...v, [key]: e.target.value }))}
                            onBlur={() =>
                                fetch("/api/settings/cookies", {
                                    method: "PUT",
                                    headers: { "Content-Type": "application/json" },
                                    body: JSON.stringify({ cookies: values }),
                                })
                            }
                        />
                    </div>
                ))}
            </div>
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
