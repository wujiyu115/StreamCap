import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Loader2, QrCode, RefreshCw, ShieldAlert } from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import { toast } from "sonner"
import { douyinApi } from "@/api"
import type { DouyinLoginState, DouyinLoginStateName } from "@/api/types"
import { Button } from "@/components/ui/button"
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { translateError, useI18n } from "@/i18n"

/** 轮询间隔：后端线程 1.5s 一轮，前端跟它同频就够 */
const POLL_MS = 1500
const TERMINAL: DouyinLoginStateName[] = ["confirmed", "failed", "cancelled", "timeout"]
const LOGIN_QUERY_KEY = ["douyin-login"]

interface DouyinLoginDialogProps {
    open: boolean
    onOpenChange: (open: boolean) => void
    /** Cookie 已写进设置（调用方拿它去刷新关注列表 / 在播列表） */
    onSuccess?: () => void
}

/**
 * 扫码登录抖音：Cookie 失效时不用再去浏览器里手动导 Cookie。
 *
 * 后端会起一个 headless chromium（伪装 UA，否则抖音直接给你滑块验证），把登录框里的
 * 二维码透出来；手机扫完确认，登录态就从浏览器 cookie jar 里取回来写进设置。
 * 这里只负责展示 + 轮询，二维码本身是后端产出的 data URL。
 */
export function DouyinLoginDialog({ open, onOpenChange, onSuccess }: DouyinLoginDialogProps) {
    const { t } = useI18n()
    const queryClient = useQueryClient()
    const [starting, setStarting] = useState(false)
    const stateRef = useRef<DouyinLoginStateName | undefined>(undefined)
    // 成功只能报一次（轮询会持续拿到 confirmed）
    const doneRef = useRef(false)

    const { data } = useQuery<DouyinLoginState>({
        queryKey: LOGIN_QUERY_KEY,
        queryFn: douyinApi.loginStatus,
        enabled: open,
        // 终态就别再打了；等扫码期间一直轮
        refetchInterval: (query) => {
            const state = query.state.data?.state
            return state && TERMINAL.includes(state) ? false : POLL_MS
        },
    })
    stateRef.current = data?.state

    const start = useCallback(async () => {
        setStarting(true)
        doneRef.current = false
        try {
            const payload = await douyinApi.loginStart()
            queryClient.setQueryData(LOGIN_QUERY_KEY, payload)
        } catch (e) {
            toast.error(e instanceof Error ? translateError(e.message) : t("common.failed"))
        } finally {
            setStarting(false)
        }
    }, [queryClient, t])

    // 打开就起一个会话（后端幂等：已经在跑就把当前状态回给我们）
    useEffect(() => {
        if (open) void start()
    }, [open, start])

    // 关掉时如果还在等扫码，得让后端收掉浏览器线程，别留着空转
    useEffect(() => {
        if (open) return
        const state = stateRef.current
        if (state && state !== "idle" && !TERMINAL.includes(state)) {
            void douyinApi.loginCancel().catch(() => {})
        }
    }, [open])

    // 登录成功：刷新 Cookie 与关注列表（新 Cookie 下缓存会重新拉）
    useEffect(() => {
        if (data?.state !== "confirmed" || !data.saved || doneRef.current) return
        doneRef.current = true
        toast.success(t("douyinLogin.success"))
        void queryClient.invalidateQueries({ queryKey: ["cookies"] })
        void queryClient.invalidateQueries({ queryKey: ["douyin-cookie"] })
        void queryClient.invalidateQueries({ queryKey: ["douyin-self"] })
        void queryClient.invalidateQueries({ queryKey: ["douyin-live"] })
        void queryClient.invalidateQueries({ queryKey: ["douyin-following"] })
        onSuccess?.()
    }, [data, onSuccess, queryClient, t])

    const state = data?.state ?? "idle"
    const running = starting || state === "starting"
    const failed = state === "failed"
    const statusText = failed
        ? (data?.error && translateError(data.error)) || t("douyinLogin.failed")
        : state === "confirmed"
          ? t("douyinLogin.confirmed")
          : (t(`douyinLogin.${state}`) as string)

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-md">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <QrCode className="h-4 w-4" />
                        {t("douyinLogin.title")}
                    </DialogTitle>
                    <DialogDescription>{t("douyinLogin.desc")}</DialogDescription>
                </DialogHeader>

                <div className="flex flex-col items-center gap-3">
                    {data?.verify && (
                        <div className="flex items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs">
                            <ShieldAlert className="h-3.5 w-3.5 shrink-0 text-amber-500" />
                            {t("douyinLogin.verify")}
                        </div>
                    )}

                    <div className="flex h-56 w-56 items-center justify-center rounded-lg border bg-white p-2">
                        {running ? (
                            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                        ) : failed || !data?.qrcode ? (
                            <span className="px-4 text-center text-xs text-muted-foreground">
                                {state === "confirmed" ? `✅ ${t("douyinLogin.confirmed")}` : statusText}
                            </span>
                        ) : (
                            <img
                                src={data.qrcode}
                                alt={t("douyinLogin.title")}
                                data-testid="douyin-login-qrcode"
                                className="h-full w-full object-contain"
                            />
                        )}
                    </div>

                    <p className="text-center text-sm" data-testid="douyin-login-status">
                        {statusText}
                    </p>
                    {data?.hint && !failed && (
                        <p className="text-center text-xs text-muted-foreground">{data.hint}</p>
                    )}
                    {state === "confirmed" && (
                        <p className="text-center text-xs text-muted-foreground">
                            {t("douyinLogin.savedHint")}
                        </p>
                    )}
                    {failed && data?.unavailable && (
                        <p className="text-center text-xs text-muted-foreground">
                            {t("douyinLogin.unavailable")}
                        </p>
                    )}
                </div>

                <div className="flex justify-end gap-2">
                    {TERMINAL.includes(state) && state !== "confirmed" && (
                        <Button variant="outline" size="sm" onClick={() => void start()}>
                            <RefreshCw className="mr-1 h-4 w-4" />
                            {t("douyinLogin.restart")}
                        </Button>
                    )}
                    <Button size="sm" onClick={() => onOpenChange(false)}>
                        {state === "confirmed" ? t("common.close") : t("common.cancel")}
                    </Button>
                </div>
            </DialogContent>
        </Dialog>
    )
}