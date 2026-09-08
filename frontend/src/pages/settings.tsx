import { useQuery } from "@tanstack/react-query"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Loader2, RotateCcw } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { settingsApi } from "@/api"
import type { SettingsData, SettingsResetResult, SettingsWriteResult } from "@/api/types"
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
import { translateError, useI18n } from "@/i18n"
import { toast } from "sonner"

export default function SettingsPage() {
    return (
        <Tabs defaultValue="recording" className="space-y-4">
            <TabsList className="w-full overflow-x-auto whitespace-nowrap sm:w-auto">
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

function CookiesTab() {
    const { t, tf } = useI18n()
    const { data, isLoading } = useQuery({
        queryKey: ["cookies"],
        queryFn: settingsApi.getCookies,
    })
    const [values, setValues] = useState<Record<string, string>>({})

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
