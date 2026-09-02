import { useEffect, useState } from "react"

import { mediaApi } from "@/api"

export interface VideoMeta {
    duration: string
    resolution: string
}

function formatDuration(seconds: number): string {
    if (!isFinite(seconds) || seconds <= 0) return "--:--"
    const h = Math.floor(seconds / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    const s = Math.floor(seconds % 60)
    const pad = (n: number) => String(n).padStart(2, "0")
    return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
}

/** 懒加载视频元数据（时长/分辨率）：离屏 video 元素 preload=metadata，
 * 不下载整个文件（Range 请求只取 moov/头部）。.ts 裸流无 metadata，返回占位。 */
export function useVideoMeta(relPath: string | null, type: string): VideoMeta | null {
    const [meta, setMeta] = useState<VideoMeta | null>(null)

    useEffect(() => {
        setMeta(null)
        if (!relPath || type !== "video") return
        if (/\.(ts|m2ts|mts|flv)$/i.test(relPath)) return

        let cancelled = false
        const video = document.createElement("video")
        video.preload = "metadata"
        video.src = mediaApi.streamUrl(relPath)
        video.onloadedmetadata = () => {
            if (cancelled) return
            setMeta({
                duration: formatDuration(video.duration),
                resolution:
                    video.videoWidth && video.videoHeight
                        ? `${video.videoWidth}×${video.videoHeight}`
                        : "",
            })
        }
        return () => {
            cancelled = true
            video.removeAttribute("src")
            video.load()
        }
    }, [relPath, type])

    return meta
}
