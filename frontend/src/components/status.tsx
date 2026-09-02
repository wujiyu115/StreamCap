import type { Recording } from "@/api/types"

export function formatDuration(totalSeconds: number): string {
    const s = Math.max(0, Math.floor(totalSeconds))
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    const sec = s % 60
    const pad = (n: number) => String(n).padStart(2, "0")
    return `${pad(h)}:${pad(m)}:${pad(sec)}`
}

export function formatDateTime(iso: string | null): string {
    if (!iso) return "-"
    const d = new Date(iso)
    const pad = (n: number) => String(n).padStart(2, "0")
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

export function formatTimestamp(seconds: number): string {
    const d = new Date(seconds * 1000)
    const pad = (n: number) => String(n).padStart(2, "0")
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const STATE_CLASS: Record<string, string> = {
    recording: "bg-green-500 hover:bg-green-500",
    error: "bg-red-500 hover:bg-red-500",
    live: "bg-blue-500 hover:bg-blue-500",
    offline: "bg-amber-500 hover:bg-amber-500",
    stopped: "bg-gray-400 hover:bg-gray-400",
    checking: "bg-purple-500 hover:bg-purple-500",
    unknown: "bg-gray-300 hover:bg-gray-300",
}

const STATE_LABEL_KEY: Record<string, string> = {
    recording: "recordings.statusRecording",
    error: "recordings.statusError",
    live: "recordings.statusLive",
    offline: "recordings.statusOffline",
    stopped: "recordings.statusStopped",
    checking: "recordings.statusChecking",
    unknown: "recordings.statusStopped",
}

export function StatusBadge({ state, label }: { state: string; label: string }) {
    return (
        <span
            className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium text-white ${
                STATE_CLASS[state] ?? STATE_CLASS.unknown
            }`}
        >
            {state === "recording" && (
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white" />
            )}
            {label}
        </span>
    )
}

export function stateLabelKey(state: string): string {
    return STATE_LABEL_KEY[state] ?? STATE_LABEL_KEY.unknown
}

export function displayDuration(rec: Pick<Recording, "is_recording" | "cumulative_duration_seconds" | "start_time" | "last_duration_seconds">): number {
    if (rec.is_recording && rec.start_time) {
        const elapsed = (Date.now() - new Date(rec.start_time).getTime()) / 1000
        return rec.cumulative_duration_seconds + elapsed
    }
    return rec.last_duration_seconds
}
