import { useQuery } from "@tanstack/react-query"
import {
    ExternalLink,
    Loader2,
    Maximize2,
    Pause,
    Play,
    RefreshCw,
    Volume2,
    VolumeX,
    X,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { douyinApi } from "@/api"
import type { DouyinLiveRoom } from "@/api/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { translateError, useI18n } from "@/i18n"

/** 起播看门狗：地址过期 / CDN 卡住 / 编码不支持都不会触发 error 事件，光等会永远转圈 */
const PLAY_WATCHDOG_MS = 15_000
/** 同一个房间里「自动重取地址」的次数上限（防在死链上无限重取） */
const MAX_AUTO_RETRY = 1

interface LivePlayerDialogProps {
    /** 要播的直播间；null 表示关闭 */
    room: DouyinLiveRoom | null
    onOpenChange: (open: boolean) => void
}

/**
 * 关注页里的「在网页里看直播」。
 *
 * 地址有两个来源：
 * 1. 列表接口 `/api/douyin/live/following-live` 返回的 `room.play_url`——关注在播那批实测是
 *    h264 全画质签名直链（1088x1920，与 room enter 同档），直接播，省一次取流请求；
 * 2. 走不到（或直链挂了）才调 `/api/douyin/live/stream` 现取（streamget room enter）。
 *
 * flv 走 mpegts.js（MSE 解封装），H.265 房间退回 hls（只有 Safari 原生能直吃）。
 * 地址会失效（主播重开播就换 stream id），所以播放出错时先回落取流接口、再自动重取一次，
 * 仍失败才报错。
 */
export function LivePlayerDialog({ room, onOpenChange }: LivePlayerDialogProps) {
    const { t } = useI18n()
    const videoRef = useRef<HTMLVideoElement | null>(null)
    const stageRef = useRef<HTMLDivElement | null>(null)
    // 播放出错的自动重取：同一个地址只重试一次，别在死链上循环打接口
    const autoRetryRef = useRef(0)
    // refetch 时要绕过后端 60s 缓存（缓存里的地址可能正是坏的那个）
    const forceRef = useRef(false)

    // 舞台宽度跟着视频盒走：抖音直播 9:16 是主流，固定 4xl 宽会在两边留一大片黑
    const [stageWidth, setStageWidth] = useState<number | null>(null)
    const [muted, setMuted] = useState(true)
    const [playing, setPlaying] = useState(false)
    const [failed, setFailed] = useState(false)
    const [isFullscreen, setIsFullscreen] = useState(false)
    // 列表接口给的直链播不起来时置 true：回落 `/live/stream`（那里能换到 h264 全画质/另一条线路）
    const [directFailed, setDirectFailed] = useState(false)

    const webRid = room?.web_rid ?? ""
    const roomUrl = webRid ? `https://live.douyin.com/${webRid}` : "https://live.douyin.com/"

    // 列表直链只有 h264 档位才会填；签名过期（expire_at 是秒级时间戳）就当作没有
    const directUrl = room?.play_url ?? ""
    const directExpired = Boolean(room?.expire_at && room.expire_at * 1000 <= Date.now())
    // 列表直链（关注在播 feed 自带的 h264 地址）在部分客户端播不起来：2026-10-30 生产日志里
    // 用户点开后直链与回落地址都在 3s 内报错，同一网络下干净 Chromium 却正常。先关掉，
    // 回到「每次打开都请求 /live/stream」的旧路径；想再启用把它改成 true（后端字段与回落逻辑都还在）。
    const USE_LIST_DIRECT_URL: boolean = false
    const usingDirect = USE_LIST_DIRECT_URL && Boolean(directUrl) && !directExpired && !directFailed
    // 回调里（onError / 看门狗）判断当前走的是哪条路，避免把旧闭包里的值当真
    const usingDirectRef = useRef(usingDirect)
    usingDirectRef.current = usingDirect

    const stream = useQuery({
        queryKey: ["douyin-live-stream", webRid],
        queryFn: () => {
            const force = forceRef.current
            forceRef.current = false
            return douyinApi.liveStream(webRid, undefined, force)
        },
        // 已经有能直解的直链就别打接口了
        enabled: Boolean(room && webRid) && !usingDirect,
        staleTime: 60_000,
        gcTime: 60_000,
        retry: 1,
        refetchOnWindowFocus: false,
    })

    const playUrl = usingDirect ? directUrl : (stream.data?.play_url ?? "")
    const playback = usingDirect ? room?.playback || "flv" : (stream.data?.playback ?? "")
    const quality = usingDirect ? (room?.quality ?? "") : (stream.data?.quality ?? "")
    // 每次成功取到地址都会变（哪怕地址字符串没变）——用它当「新一轮尝试」的信号
    const { dataUpdatedAt } = stream

    // 只有 Safari / iOS 上的浏览器（含 iOS 版 Chrome、Firefox，都是 WebKit）和 Android Chrome
    // 原生吃 hls。**不能只用 canPlayType 判断**：桌面 Chromium 对 mpegurl 也回 "maybe"，
    // 实测把 m3u8 灌进 <video> 只是永远黑屏（没有 hls.js 时只能引导去抖音看）。
    const nativeHls = useMemo(() => {
        if (typeof navigator === "undefined" || typeof document === "undefined") return false
        if (!document.createElement("video").canPlayType("application/vnd.apple.mpegurl")) return false
        const ua = navigator.userAgent
        const isIOS =
            /iPad|iPhone|iPod/.test(ua) || (/Macintosh/.test(ua) && (navigator.maxTouchPoints ?? 0) > 1)
        const isSafari = /Safari\//.test(ua) && !/Chrome\/|Chromium\/|Edg\/|Firefox\//.test(ua)
        return isIOS || isSafari || /Android/.test(ua)
    }, [])

    const reload = useCallback(() => {
        autoRetryRef.current = 0
        setFailed(false)
        // 手动重试是显式动作：列表直链既然坏过就别再试它，直接走取流接口
        setDirectFailed(true)
        forceRef.current = true
        void stream.refetch()
    }, [stream])

    // 每取到一次地址就清掉失败态（重试成功要能恢复），播放器也会重建
    useEffect(() => {
        setFailed(false)
        setPlaying(false)
    }, [webRid, dataUpdatedAt])

    // 直链失败态只按房间复位：**不能跟着 dataUpdatedAt 复位**，否则「回落取流→重渲染→
    // 又去试坏直链→又失败」会在这两条路之间来回弹
    useEffect(() => {
        setDirectFailed(false)
    }, [webRid])

    // 自动重取预算只按房间复位，**不随地址变化复位**：否则「新地址→预算清零→又失败→又重取」
    // 会变成每 15s 无限重取。用户点「重试」是显式动作，另行复位。
    useEffect(() => {
        autoRetryRef.current = 0
    }, [webRid])

    // 量视频盒（已被 max-height 约束过的真实大小）回写给舞台，让卡片贴着画面
    const syncStageWidth = useCallback(() => {
        const video = videoRef.current
        if (!video || !video.videoWidth) return
        // 移动端是「铺满视口」的沉浸布局，别掺和
        if (window.innerWidth <= 640) {
            setStageWidth(null)
            return
        }
        setStageWidth(Math.round(video.getBoundingClientRect().width))
    }, [])

    // 装载播放源：flv → mpegts.js；hls → 原生 src（仅 Safari）
    useEffect(() => {
        const video = videoRef.current
        if (!room || !video || !playUrl) return
        if (playback === "hls" && !nativeHls) return

        let cancelled = false
        let player: { destroy: () => void } | null = null

        const safePlay = () => video.play().catch(() => undefined)
        const onPlaying = () => setPlaying(true)
        const onPause = () => setPlaying(false)
        // 地址过期/主播重开播 → 先回落取流接口；回落来的地址再出错才动用重取预算
        const onError = () => {
            if (cancelled) return
            if (usingDirectRef.current) {
                setDirectFailed(true)
                return
            }
            if (autoRetryRef.current < 1) {
                autoRetryRef.current += 1
                forceRef.current = true
                void stream.refetch()
                return
            }
            setFailed(true)
        }

        video.addEventListener("playing", onPlaying)
        video.addEventListener("pause", onPause)
        video.addEventListener("error", onError)
        video.addEventListener("loadedmetadata", syncStageWidth)
        video.addEventListener("resize", syncStageWidth)

        if (playback === "flv") {
            import("mpegts.js")
                .then(({ default: mpegts }) => {
                    if (cancelled) return
                    if (!mpegts.isSupported()) {
                        setFailed(true)
                        return
                    }
                    const p = mpegts.createPlayer(
                        { type: "flv", isLive: true, url: playUrl, hasAudio: true },
                        {
                            enableStashBuffer: false,
                            stashInitialSize: 128,
                            liveBufferLatencyChasing: true,
                            autoCleanupSourceBuffer: true,
                        },
                    )
                    p.on(mpegts.Events.ERROR, onError)
                    p.attachMediaElement(video)
                    p.load()
                    safePlay()
                    player = p
                })
                .catch(() => {
                    if (!cancelled) setFailed(true)
                })
        } else {
            video.src = playUrl
            safePlay()
        }

        return () => {
            cancelled = true
            video.removeEventListener("playing", onPlaying)
            video.removeEventListener("pause", onPause)
            video.removeEventListener("error", onError)
            video.removeEventListener("loadedmetadata", syncStageWidth)
            video.removeEventListener("resize", syncStageWidth)
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
        // stream.refetch 每次渲染都是新引用，不能进依赖，否则播放器会被反复重建
        // eslint-disable-next-line react-hooks/exhaustive-deps
        // dataUpdatedAt：同地址重试也要重建播放器（旧的 mpegts 实例已经停在失败态了）
    }, [room, playUrl, playback, nativeHls, syncStageWidth, dataUpdatedAt])

    // 窗口尺寸变了（竖屏→横屏、改窗口大小）重新量一次
    useEffect(() => {
        if (!room) return
        const onResize = () => syncStageWidth()
        window.addEventListener("resize", onResize)
        return () => window.removeEventListener("resize", onResize)
    }, [room, syncStageWidth])

    // 起播看门狗：15s 还没起来就先当作地址过期重取一次（沿用「只重试一次」预算），
    // 再等 15s 还起不来就报失败——否则死链/卡流会一直转圈，用户不知道该干嘛。
    useEffect(() => {
        if (!room || !playUrl || failed) return
        const timer = setTimeout(() => {
            const video = videoRef.current
            // readyState >= 3 就算起来了（用户手动暂停时 readyState 仍是 4，别误判）
            if (video && video.readyState >= 3) return
            if (usingDirectRef.current) {
                setDirectFailed(true)
                return
            }
            if (autoRetryRef.current < MAX_AUTO_RETRY) {
                autoRetryRef.current += 1
                forceRef.current = true
                void stream.refetch()
                return
            }
            setFailed(true)
        }, PLAY_WATCHDOG_MS)
        return () => clearTimeout(timer)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [room, playUrl, failed, dataUpdatedAt])

    // 静音状态同步到元素（自动播放策略：先静音起播，用户点一下再放声）
    useEffect(() => {
        if (videoRef.current) videoRef.current.muted = muted
    }, [muted, playUrl])

    // 播放器打开期间锁页级滚动（overlay 在 main 里，滚轮会链式滚走背景页）
    useEffect(() => {
        if (!room) return
        const main = document.querySelector("main")
        if (!main) return
        const prev = main.style.overflow
        main.style.overflow = "hidden"
        return () => {
            main.style.overflow = prev
        }
    }, [room])

    // 全屏状态同步（Esc 退出全屏时收通知）
    useEffect(() => {
        const onFs = () => setIsFullscreen(Boolean(document.fullscreenElement))
        document.addEventListener("fullscreenchange", onFs)
        return () => document.removeEventListener("fullscreenchange", onFs)
    }, [])

    useEffect(() => {
        if (!room || typeof document === "undefined") return
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") onOpenChange(false)
        }
        document.addEventListener("keydown", onKey)
        return () => document.removeEventListener("keydown", onKey)
    }, [room, onOpenChange])

    const togglePlay = useCallback(() => {
        const video = videoRef.current
        if (!video) return
        if (video.paused) video.play().catch(() => undefined)
        else video.pause()
    }, [])

    const toggleFullscreen = useCallback(async () => {
        try {
            if (document.fullscreenElement) await document.exitFullscreen()
            else if (stageRef.current) await stageRef.current.requestFullscreen()
        } catch {
            /* 浏览器限制（如非用户手势）时静默 */
        }
    }, [])

    if (!room) return null

    const unsupported = playback === "hls" && !nativeHls
    // 直链来源时还没拉接口，默认就是「在播」（列表只给在播房间），标题/主播取卡片上的
    const isLive = usingDirect ? true : stream.data ? stream.data.is_live : true
    const hasSource = Boolean(playUrl) && !unsupported && isLive
    const title = (usingDirect ? "" : stream.data?.title) || room.title || room.anchor.nickname || room.web_rid
    const anchor = (usingDirect ? "" : stream.data?.anchor_name) || room.anchor.nickname || room.web_rid

    // 覆盖层一次只显示一种状态：加载中 / 接口错 / 未开播 / 播不了
    // 走直链时没有接口状态可看，只有「播不了」（失败态由播放器自己置）
    const overlay: "loading" | "error" | "notLive" | "failed" | null = usingDirect
        ? unsupported || failed || !playUrl
            ? "failed"
            : null
        : stream.error
          ? "error"
          : !stream.data
            ? "loading"
            : !stream.data.is_live
              ? "notLive"
              : unsupported || failed || !playUrl
                ? "failed"
                : null
    const errorMessage =
        (stream.error instanceof Error ? translateError(stream.error.message) : "") ||
        t("following.playerFailed")

    // 工具栏按钮：桌面 32px、移动端 44px 触控目标（与录像播放器一致）
    const iconBtn =
        "h-11 w-11 shrink-0 border-white/20 bg-white/15 text-white hover:bg-white/30 hover:text-white md:h-8 md:w-8"
    const smBtn = "border-white/20 bg-white/15 text-white hover:bg-white/30 hover:text-white"

    return (
        <div
            className="player-overlay fixed inset-0 z-50 grid place-items-center bg-black/90 p-2 backdrop-blur-sm sm:p-6"
            role="dialog"
            aria-modal="true"
            onClick={(e) => {
                if (e.target === e.currentTarget) onOpenChange(false)
            }}
        >
            <div
                ref={stageRef}
                style={stageWidth ? { width: stageWidth } : undefined}
                className="player-stage relative w-full max-w-4xl overflow-hidden rounded-lg border border-white/10 bg-black shadow-2xl"
            >
                <div className="player-stagebar absolute inset-x-0 top-0 z-10 flex items-start justify-between gap-2 bg-gradient-to-b from-black/80 to-transparent p-3">
                    <div className="min-w-0">
                        <div className="flex items-center gap-2">
                            <Badge className="shrink-0 bg-red-500 text-white hover:bg-red-500">
                                {t("following.liveNow")}
                            </Badge>
                            {quality && (
                                <Badge className="shrink-0 bg-white/15 text-white hover:bg-white/15">
                                    {quality}
                                </Badge>
                            )}
                        </div>
                        <div className="mt-1 line-clamp-1 text-sm font-medium text-white">{title}</div>
                        <div className="line-clamp-1 text-xs text-white/70">{anchor}</div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                        <Button
                            size="icon"
                            variant="outline"
                            className={iconBtn}
                            title={t("following.playerRefresh")}
                            aria-label={t("following.playerRefresh")}
                            onClick={reload}
                        >
                            <RefreshCw className="h-4 w-4" />
                        </Button>
                        <Button
                            size="icon"
                            variant="outline"
                            className={iconBtn}
                            title={t("common.close")}
                            aria-label={t("common.close")}
                            onClick={() => onOpenChange(false)}
                        >
                            <X className="h-4 w-4" />
                        </Button>
                    </div>
                </div>

                <video
                    ref={videoRef}
                    className="player-media player-media-live cursor-pointer bg-black"
                    playsInline
                    autoPlay
                    controls={false}
                    onClick={hasSource ? togglePlay : undefined}
                />

                {(overlay === "loading" || (hasSource && !playing && !failed)) && (
                    <div className="absolute inset-0 grid place-items-center bg-black/40 text-white">
                        <div className="flex items-center gap-2 text-sm">
                            <Loader2 className="h-4 w-4 animate-spin" />
                            {t("following.playerLoading")}
                        </div>
                    </div>
                )}

                {overlay === "notLive" && (
                    <div className="absolute inset-0 grid place-items-center gap-3 bg-black/70 p-6 text-center text-sm text-white">
                        <div>{t("following.playerNotLive")}</div>
                        <div className="flex flex-wrap items-center justify-center gap-2">
                            <Button size="sm" variant="outline" className={smBtn} onClick={reload}>
                                <RefreshCw className="mr-1 h-4 w-4" />
                                {t("following.playerRetry")}
                            </Button>
                            <Button size="sm" variant="outline" className={smBtn} asChild>
                                <a href={roomUrl} target="_blank" rel="noreferrer">
                                    <ExternalLink className="mr-1 h-4 w-4" />
                                    {t("following.openRoom")}
                                </a>
                            </Button>
                        </div>
                    </div>
                )}

                {(overlay === "failed" || overlay === "error") && (
                    <div className="absolute inset-0 grid place-items-center gap-3 bg-black/70 p-6 text-center text-sm text-white">
                        <div className="space-y-1">
                            <div>
                                {overlay === "error"
                                    ? errorMessage
                                    : unsupported
                                      ? t("following.playerUnsupported")
                                      : t("following.playerFailed")}
                            </div>
                            <div className="text-xs text-white/60">
                                {t("following.playerFallbackHint")}
                            </div>
                        </div>
                        <div className="flex flex-wrap items-center justify-center gap-2">
                            <Button size="sm" variant="outline" className={smBtn} onClick={reload}>
                                <RefreshCw className="mr-1 h-4 w-4" />
                                {t("following.playerRetry")}
                            </Button>
                            <Button size="sm" variant="outline" className={smBtn} asChild>
                                <a href={roomUrl} target="_blank" rel="noreferrer">
                                    <ExternalLink className="mr-1 h-4 w-4" />
                                    {t("following.openRoom")}
                                </a>
                            </Button>
                        </div>
                    </div>
                )}

                {/* 兜底面板要能点：失败/未开播时收起底部工具栏，否则它盖住「重试」按钮 */}
                {hasSource && (overlay === null || overlay === "loading") && (
                    <div className="player-stagebar absolute inset-x-0 bottom-0 z-10 flex items-center justify-center gap-2 bg-gradient-to-t from-black/80 to-transparent p-3">
                        <Button
                            size="icon"
                            variant="outline"
                            className={iconBtn}
                            title={playing ? t("following.playerPause") : t("following.playerPlay")}
                            aria-label={playing ? t("following.playerPause") : t("following.playerPlay")}
                            onClick={togglePlay}
                        >
                            {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                        </Button>
                        <Button
                            size="icon"
                            variant="outline"
                            className={iconBtn}
                            title={muted ? t("following.playerUnmute") : t("following.playerMute")}
                            aria-label={muted ? t("following.playerUnmute") : t("following.playerMute")}
                            onClick={() => setMuted((m) => !m)}
                        >
                            {muted ? <VolumeX className="h-4 w-4" /> : <Volume2 className="h-4 w-4" />}
                        </Button>
                        <Button
                            size="icon"
                            variant="outline"
                            className={iconBtn}
                            title={t("following.playerFullscreen")}
                            aria-label={t("following.playerFullscreen")}
                            onClick={toggleFullscreen}
                        >
                            <Maximize2 className={`h-4 w-4 ${isFullscreen ? "opacity-60" : ""}`} />
                        </Button>
                        <Button size="sm" variant="outline" className={smBtn} asChild>
                            <a href={roomUrl} target="_blank" rel="noreferrer">
                                <ExternalLink className="mr-1 h-3.5 w-3.5" />
                                {t("following.openRoom")}
                            </a>
                        </Button>
                    </div>
                )}
            </div>
        </div>
    )
}