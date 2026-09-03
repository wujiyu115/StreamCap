import { ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Maximize, Minimize, RotateCcw, Smartphone, Trash2 } from "lucide-react"
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

/** 媒体预览弹窗（Frostcast 布局）：
 * - 工具栏悬浮在视频顶部（渐变黑底，不占布局、避开原生 controls）
 * - 可折叠：收起后只留展开小按钮
 * - 全屏按钮：整个舞台进 Fullscreen API，工具栏仍可操作
 * - 强制横屏：竖屏视频 CSS 旋转 90°；全屏内尝试系统 orientation lock
 * - ←/→ 键切换上一个/下一个，Esc 关闭 */
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
    const stageRef = useRef<HTMLDivElement | null>(null)
    const [rotate, setRotate] = useState(false)
    const [barOpen, setBarOpen] = useState(true)
    const [isFullscreen, setIsFullscreen] = useState(false)

    // 键盘导航与 Esc 关闭
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") {
                // 全屏态下 Esc 先退全屏（浏览器默认行为），非全屏关弹窗
                if (!document.fullscreenElement) onClose()
            } else if (e.key === "ArrowLeft" && hasPrev) onPrev()
            else if (e.key === "ArrowRight" && hasNext) onNext()
        }
        document.addEventListener("keydown", onKey)
        return () => document.removeEventListener("keydown", onKey)
    }, [onClose, onPrev, onNext, hasPrev, hasNext])

    // fullscreenchange 同步状态（Esc 退出全屏时收通知）
    useEffect(() => {
        const onFs = () => setIsFullscreen(Boolean(document.fullscreenElement))
        document.addEventListener("fullscreenchange", onFs)
        return () => document.removeEventListener("fullscreenchange", onFs)
    }, [])

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

    const toggleFullscreen = useCallback(async () => {
        try {
            if (document.fullscreenElement) {
                await document.exitFullscreen()
            } else if (stageRef.current) {
                await stageRef.current.requestFullscreen()
            }
        } catch {
            /* 浏览器限制（如非用户手势）时静默 */
        }
    }, [])

    // 强制横屏：CSS 旋转；全屏内尝试系统 orientation lock
    const toggleRotate = useCallback(async () => {
        const next = !rotate
        setRotate(next)
        try {
            const orientation = screen.orientation as ScreenOrientation & {
                lock?: (o: string) => Promise<void>
            }
            if (orientation?.lock && document.fullscreenElement) {
                await orientation.lock(next ? "landscape" : "portrait")
            }
        } catch {
            /* 仅全屏内可锁，失败走纯 CSS 旋转 */
        }
    }, [rotate])

    // 退出全屏（关闭弹窗/切媒体时避免残留全屏）
    const exitFullscreen = useCallback(() => {
        if (document.fullscreenElement) document.exitFullscreen().catch(() => undefined)
    }, [])
    useEffect(() => {
        return exitFullscreen
    }, [exitFullscreen])

    const isImage = item.type === "image"
    const metaParts = [item.ext, resolution, duration].filter(Boolean).join(" · ")

    const iconBtn =
        "h-10 w-10 shrink-0 border-white/20 bg-white/15 text-white hover:bg-white/30 hover:text-white disabled:opacity-30"

    return (
        <div
            className="player-overlay fixed inset-0 z-50 grid place-items-center bg-black/90 p-2 backdrop-blur-sm sm:p-6"
            role="dialog"
            aria-modal="true"
            onClick={(e) => {
                if (e.target === e.currentTarget) onClose()
            }}
        >
            {/* 舞台：全屏与旋转都作用于它（工具栏随之）。
                桌面为居中卡片；移动端（<sm）由 CSS 改为全屏沉浸布局 */}
            <div
                ref={stageRef}
                className={`player-stage relative w-full max-w-4xl overflow-hidden rounded-lg border border-white/10 bg-black shadow-2xl ${
                    rotate ? "rot" : ""
                }`}
            >
                {isImage ? (
                    <img
                        src={streamUrl(item.rel_path)}
                        alt={item.name}
                        className="player-media h-full w-full object-contain"
                        onClick={toggleRotate}
                    />
                ) : (
                    <video
                        ref={videoRef}
                        controls
                        playsInline
                        className="player-media h-full w-full bg-black"
                    />
                )}

                {/* 悬浮工具栏：顶部渐变，收起后只留展开按钮 */}
                <div
                    className={
                        barOpen
                            ? "absolute inset-x-0 top-0 z-10 flex items-center gap-2 bg-gradient-to-b from-black/70 to-transparent p-3"
                            : "absolute inset-x-0 top-0 z-10 flex justify-end p-2"
                    }
                >
                    {barOpen && (
                        <>
                            <div className="min-w-0 flex-1" onClick={(e) => e.stopPropagation()}>
                                <div className="truncate text-sm font-semibold text-white drop-shadow">
                                    {item.name}
                                </div>
                                <div className="truncate text-xs text-white/70">
                                    {metaParts || "—"}
                                    {item.size ? ` · ${item.size}` : ""}
                                </div>
                            </div>
                            <Button
                                variant="outline"
                                size="icon"
                                className={iconBtn}
                                title={t("media.prev")}
                                disabled={!hasPrev}
                                onClick={onPrev}
                            >
                                <ChevronLeft className="h-5 w-5" />
                            </Button>
                            <Button
                                variant="outline"
                                size="icon"
                                className={iconBtn}
                                title={t("media.next")}
                                disabled={!hasNext}
                                onClick={onNext}
                            >
                                <ChevronRight className="h-5 w-5" />
                            </Button>
                            {!isImage && (
                                <Button
                                    variant="outline"
                                    size="icon"
                                    className={iconBtn}
                                    title={rotate ? t("media.rotateBack") : t("media.rotate")}
                                    onClick={toggleRotate}
                                >
                                    {/* 手机旋转图标：与全屏 Maximize 明确区分 */}
                                    <Smartphone className="h-5 w-5 -rotate-90" />
                                </Button>
                            )}
                            {isImage && (
                                <Button
                                    variant="outline"
                                    size="icon"
                                    className={iconBtn}
                                    title={t("media.rotate")}
                                    onClick={toggleRotate}
                                >
                                    <RotateCcw className="h-5 w-5" />
                                </Button>
                            )}
                            {!isImage && (
                                <Button
                                    variant="outline"
                                    size="icon"
                                    // 移动端不显示全屏按钮：原生 controls 自带、旋转场景下行为冲突
                                    className={`${iconBtn} hidden md:inline-flex`}
                                    title={isFullscreen ? t("media.exitFullscreen") : t("media.fullscreen")}
                                    onClick={toggleFullscreen}
                                >
                                    {isFullscreen ? (
                                        <Minimize className="h-5 w-5" />
                                    ) : (
                                        <Maximize className="h-5 w-5" />
                                    )}
                                </Button>
                            )}
                            <Button
                                variant="outline"
                                size="icon"
                                className={`${iconBtn} border-red-400/40 bg-red-500/30 hover:bg-red-500/50`}
                                title={t("common.delete")}
                                onClick={() => onDelete(item.rel_path)}
                            >
                                <Trash2 className="h-5 w-5" />
                            </Button>
                            <Button
                                variant="outline"
                                size="icon"
                                className={iconBtn}
                                title={t("common.close")}
                                onClick={() => {
                                    exitFullscreen()
                                    onClose()
                                }}
                            >
                                <ChevronDown className="h-5 w-5" />
                            </Button>
                        </>
                    )}
                    <Button
                        variant="outline"
                        size="icon"
                        className={
                            barOpen
                                ? `${iconBtn} h-8 w-8`
                                : "h-8 w-8 border-white/20 bg-black/40 text-white hover:bg-black/60"
                        }
                        title={barOpen ? t("media.collapseBar") : t("media.expandBar")}
                        onClick={() => setBarOpen((o) => !o)}
                    >
                        {barOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                    </Button>
                </div>
            </div>
        </div>
    )
}
