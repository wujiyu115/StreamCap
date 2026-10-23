import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Loader2, QrCode, RefreshCw } from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import { toast } from "sonner"
import { bilibiliApi } from "@/api"
import type { BilibiliLoginState } from "@/api/types"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { useI18n } from "@/i18n"

const TERMINAL = ["confirmed", "failed", "cancelled", "timeout"]
const KEY = ["bilibili-login"]

export function BilibiliLoginDialog({ open, onOpenChange, onSuccess }: { open: boolean; onOpenChange: (v: boolean) => void; onSuccess?: () => void }) {
    const { t } = useI18n()
    const client = useQueryClient()
    const [starting, setStarting] = useState(false)
    const done = useRef(false)
    const query = useQuery<BilibiliLoginState>({
        queryKey: KEY, queryFn: bilibiliApi.loginStatus, enabled: open,
        refetchInterval: (q) => TERMINAL.includes(q.state.data?.state ?? "") ? false : 1500,
    })
    const start = useCallback(async () => {
        setStarting(true); done.current = false
        try { client.setQueryData(KEY, await bilibiliApi.loginStart()) }
        catch { toast.error(t("bilibiliLogin.failed")) }
        finally { setStarting(false) }
    }, [client, t])
    useEffect(() => { if (open) void start() }, [open, start])
    useEffect(() => {
        if (open || TERMINAL.includes(query.data?.state ?? "idle")) return
        void bilibiliApi.loginCancel().catch(() => {})
    }, [open, query.data?.state])
    useEffect(() => {
        if (query.data?.state !== "confirmed" || !query.data.saved || done.current) return
        done.current = true; toast.success(t("bilibiliLogin.success")); onSuccess?.()
        void client.invalidateQueries({ queryKey: ["uploads"] })
    }, [query.data, client, onSuccess, t])
    const state = query.data?.state ?? "idle"
    return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="max-w-md">
        <DialogHeader><DialogTitle className="flex items-center gap-2"><QrCode className="h-4 w-4" />{t("bilibiliLogin.title")}</DialogTitle><DialogDescription>{t("bilibiliLogin.desc")}</DialogDescription></DialogHeader>
        <div className="flex flex-col items-center gap-3"><div className="flex h-56 w-56 items-center justify-center rounded-lg border bg-white p-2">
            {starting || state === "starting" ? <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /> : query.data?.qrcode ? <img src={query.data.qrcode} alt="Bilibili QR" className="h-full w-full object-contain" /> : <span className="text-sm text-muted-foreground">{t(`bilibiliLogin.${state}`)}</span>}
        </div><p className="text-sm">{t(`bilibiliLogin.${state}`)}</p></div>
        <div className="flex justify-end gap-2">{TERMINAL.includes(state) && state !== "confirmed" && <Button variant="outline" size="sm" onClick={() => void start()}><RefreshCw className="mr-1 h-4 w-4" />{t("bilibiliLogin.restart")}</Button>}<Button size="sm" onClick={() => onOpenChange(false)}>{t("common.close")}</Button></div>
    </DialogContent></Dialog>
}
