import { ChevronLeft, ChevronRight, Maximize, RotateCcw, Smartphone, Trash2, X } from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"

import type { MediaItem } from "@/api/types"
import { Button } from "@/components/ui/button"
import { useI18n } from "@/i18n"

const TS_RE = /\.(ts|m2ts|mts|flv)$/i

interface PlayerDialogProps {
    item: MediaItem
    streamUrl: (relPath: string) => string
    hasPrev: boolean
    hasNext: boolean
    onPrev: () => void
    onNext: () => void
    onClose: () => void
    onDelete: (relPath: string) => void
    duration?: string
    resolution?: string
}

/** 媒体预览弹窗：视频/图片 + 工具栏（上一个/下一个/横屏/删除/关闭，←/→ 键盘导航）。 */
export function PlayerDialog({
    item,
    streamUrl,
    hasPrev,
    hasNext,
    onPrev,
    onNext,
    onClose,
    onDelete,
    duration,
    resolution,
}: PlayerDialogProps) {
    const { t } = useI18n()
    const videoRef = useRef<HTMLVideoElement | null>(null)
    const [rotate, setRotate] = useState(false)

    // 键盘导航与 Esc 关闭
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") onClose()
            else if (e.key === "ArrowLeft" && hasPrev) onPrev()
            else if (e.key === "ArrowRight" && hasNext) onNext()
        }
        document.addEventListener("keydown", onKey)
        return () => document.removeEventListener("keydown", onKey)
    }, [onClose, onPrev, onNext, hasPrev, hasNext])

    // 切换媒体时复位旋转
    useEffect(() => {
        setRotate(false)
    }, [item.rel_path])

    // 视频源装载：.ts/.flv 走 mpegts.js（MSE 解封装），其余原生 src；失败回退原生
    useEffect(() => {
        if (item.type !== "video") return
        const video = videoRef.current
        if (!video) return
        const url = streamUrl(item.rel_path)
        let player: { destroy: () => void } | null = null
        let cancelled = false

        const safePlay = (v: HTMLVideoElement) => {
            v.play().catch(() => undefined)
        }

        if (TS_RE.test(item.name)) {
            import("mpegts.js")
                .then(({ default: mpegts }) => {
                    if (cancelled || !videoRef.current || !mpegts.isSupported()) return
                    const p = mpegts.createPlayer(
                        { type: "mpegts", url, isLive: false },
                        { enableStashBuffer: false },
                    )
                    p.on(mpegts.Events.ERROR, () => {
                        if (cancelled) return
                        try {
                            p.destroy()
                        } catch {
                            /* noop */
                        }
                        player = null
                        video.src = url
                        safePlay(video)
                    })
                    p.attachMediaElement(video)
                    p.load()
                    safePlay(video)
                    player = p
                })
                .catch(() => {
                    video.src = url
                    safePlay(video)
                })
        } else {
            video.src = url
            safePlay(video)
        }

        return () => {
            cancelled = true
            if (player) {
                try {
                    player.destroy()
                } catch {
                    /* noop */
                }
            }
            video.removeAttribute("src")
            video.load()
        }
    }, [item.rel_path, item.name, item.type, streamUrl])

    const isImage = item.type === "image"
    const metaParts = [item.ext, resolution, duration].filter(Boolean).join(" · ")

    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 p-2 sm:p-6"
            role="dialog"
            aria-modal="true"
            onClick={(e) => {
                if (e.target === e.currentTarget) onClose()
            }}
        >
            <div className="flex max-h-full w-full max-w-4xl flex-col gap-3">
                {/* 舞台 */}
                <div className="flex min-h-0 flex-1 items-center justify-center">
                    {isImage ? (
                        <img
                            src={streamUrl(item.rel_path)}
                            alt={item.name}
                            className="max-h-[70dvh] max-w-full rounded-md object-contain"
                        />
                    ) : (
                        <video
                            ref={videoRef}
                            controls
                            playsInline
                            className={
                                rotate
                                    ? "max-h-none w-[85dvh] rotate-90 rounded-md bg-black"
                                    : "max-h-[62dvh] w-full rounded-md bg-black"
                            }
                        />
                    )}
                </div>

                {/* 工具栏 */}
                <div className="flex items-center gap-2 rounded-lg bg-background/95 p-2 shadow-lg">
                    <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium">{item.name}</div>
                        <div className="truncate text-xs text-muted-foreground">
                            {metaParts || "—"}
                            {item.size ? ` · ${item.size}` : ""}
                        </div>
                    </div>
                    <Button variant="outline" size="icon" title={t("media.prev")} disabled={!hasPrev} onClick={onPrev}>
                        <ChevronLeft className="h-4 w-4" />
                    </Button>
                    <Button variant="outline" size="icon" title={t("media.next")} disabled={!hasNext} onClick={onNext}>
                        <ChevronRight className="h-4 w-4" />
                    </Button>
                    {!isImage && (
                        <Button
                            variant="outline"
                            size="icon"
                            title={rotate ? t("media.rotateBack") : t("media.rotate")}
                            onClick={() => setRotate((r) => !r)}
                        >
                            {rotate ? <Smartphone className="h-4 w-4" /> : <Maximize className="h-4 w-4" />}
                        </Button>
                    )}
                    {isImage && (
                        <Button
                            variant="outline"
                            size="icon"
                            title={t("media.rotate")}
                            onClick={() => setRotate((r) => !r)}
                        >
                            <RotateCcw className="h-4 w-4" />
                        </Button>
                    )}
                    <Button
                        variant="destructive"
                        size="icon"
                        title={t("common.delete")}
                        onClick={() => onDelete(item.rel_path)}
                    >
                        <Trash2 className="h-4 w-4" />
                    </Button>
                    <Button variant="outline" size="icon" title={t("common.close")} onClick={onClose}>
                        <X className="h-4 w-4" />
                    </Button>
                </div>
            </div>
        </div>
    )
}
