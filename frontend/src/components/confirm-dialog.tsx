import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { createContext, useCallback, useContext, useRef, useState } from "react"
import type { ReactNode } from "react"

import { useI18n } from "@/i18n"

interface ConfirmOptions {
    /** 对话框正文（现有 confirm 文案是完整问句，直接作 description） */
    description: string
    /** 确认按钮文字；缺省用通用「确认」 */
    confirmText?: string
    /** destructive 时确认按钮红色（删除类操作） */
    destructive?: boolean
}

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>

interface ConfirmContextValue {
    confirm: ConfirmFn
}

const ConfirmContext = createContext<ConfirmContextValue>({ confirm: () => Promise.resolve(false) })

interface PendingState extends ConfirmOptions {
    resolve: (ok: boolean) => void
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
    const { t } = useI18n()
    const [pending, setPending] = useState<PendingState | null>(null)
    // 叠加调用时后开的先关：只保留一个待决 promise，用 ref 指向最新的 resolve
    const latestRef = useRef<((ok: boolean) => void) | null>(null)

    const confirm = useCallback<ConfirmFn>((options) => {
        return new Promise<boolean>((resolve) => {
            latestRef.current?.(false)
            latestRef.current = resolve
            setPending({ ...options, resolve })
        })
    }, [])

    const settle = (ok: boolean) => {
        setPending(null)
        latestRef.current = null
        pending?.resolve(ok)
    }

    return (
        <ConfirmContext.Provider value={{ confirm }}>
            {children}
            <AlertDialog
                open={pending !== null}
                onOpenChange={(open) => {
                    if (!open) settle(false)
                }}
            >
                <AlertDialogContent className="max-w-sm gap-3 p-4 sm:p-6">
                    <AlertDialogHeader className="space-y-1.5">
                        <AlertDialogTitle className="text-base">
                            {t("common.confirm")}
                        </AlertDialogTitle>
                        <AlertDialogDescription className="leading-relaxed">
                            {pending?.description}
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter className="gap-2 sm:space-x-0">
                        <AlertDialogCancel
                            onClick={() => settle(false)}
                            className="mt-0 flex-1 sm:flex-none"
                        >
                            {t("common.cancel")}
                        </AlertDialogCancel>
                        <AlertDialogAction
                            onClick={() => settle(true)}
                            className={`flex-1 border-transparent sm:flex-none ${
                                pending?.destructive
                                    ? "bg-destructive text-white hover:bg-destructive/90"
                                    : ""
                            }`}
                        >
                            {pending?.confirmText ?? t("common.confirm")}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </ConfirmContext.Provider>
    )
}

export function useConfirm(): ConfirmFn {
    return useContext(ConfirmContext).confirm
}
