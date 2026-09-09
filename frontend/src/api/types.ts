export interface Recording {
    rec_id: string
    url: string
    streamer_name: string
    record_format: string
    quality: string
    segment_record: boolean
    segment_time: number | string
    monitor_status: boolean
    scheduled_recording: boolean
    scheduled_start_time: string | null
    monitor_hours: string | null
    recording_dir: string | null
    media_path: string | null
    enabled_message_push: boolean
    only_notify_no_record: boolean
    special_attention: boolean
    flv_use_direct_download: boolean
    video_bitrate: number | null
    pose_enabled: boolean | null
    platform: string | null
    platform_key: string | null
    title: string
    display_title: string
    live_title: string | null
    status_info: string | null
    is_live: boolean
    is_recording: boolean
    is_checking: boolean
    manually_stopped: boolean
    stopping_in_progress: boolean
    speed: string
    start_time: string | null
    cumulative_duration_seconds: number
    last_duration_seconds: number
    scheduled_time_range: string[] | null
    record_url: string | null
    current_output_file: string | null
    state: "recording" | "error" | "live" | "offline" | "stopped" | "checking" | "unknown"
    unsupported?: boolean
    consecutive_failures?: number
    created_at?: number | null
    last_live_time?: number | null
    live_count?: number
    avg_live_interval?: number | null
}

export interface RecordingStatusSnapshot {
    rec_id: string
    state: Recording["state"]
    status_info: string | null
    is_recording: boolean
    is_live: boolean
    is_checking: boolean
    monitor_status: boolean
    speed: string
    live_title: string | null
    start_time: string | null
    cumulative_duration_seconds: number
    last_duration_seconds: number
}

export interface RecordingStatusesResponse {
    recordings: RecordingStatusSnapshot[]
    recording_enabled: boolean
    server_time: string
}

export interface ValidityCheckResult {
    rec_id: string
    streamer_name: string
    url: string
    platform: string | null
    platform_key: string | null
    status: "live" | "offline" | "invalid" | "error"
    anchor_name: string | null
    title: string | null
    detail: string | null
    precise: boolean
    checked_at?: number
    cached?: boolean
}

export interface ValidityCheckResponse {
    results: ValidityCheckResult[]
    not_found: string[]
    pending: number
}

export type FailureReason = "transient" | "repeated" | "unsupported" | "invalid"

export type FailureReasonCounts = Record<FailureReason, number>

export interface AnalyticsOverview {
    days: number
    summary: {
        sessions: number
        seconds: number
        files: number
        bytes: number
        active_anchors: number
        monitoring: number
        checks: number
        check_failures: number
        sessions_prev: number
        sessions_change_pct: number | null
        starts: number
        aborts: number
        notify_only: number
        recordable_sessions: number
        record_coverage: number | null
        abort_rate: number | null
    }
    trend: { date: string; sessions: number; seconds: number; files: number; bytes: number }[]
    rankings: {
        top_sessions: { rec_id: string; name: string; sessions: number; seconds: number; files: number; bytes: number }[]
        top_single_day: { rec_id: string; name: string; date: string; seconds: number }[]
        top_frequency: { rec_id: string; name: string; live_count: number; avg_interval_hours: number | null }[]
        top_bytes: {
            rec_id: string
            name: string
            bytes: number
            files: number
            seconds: number
            bytes_per_hour: number | null
        }[]
        top_failures: { rec_id: string; name: string; checks: number; failures: number; failure_rate: number | null }[]
    }
    idle: { rec_id: string; name: string; idle_days: number; days_left: number | null }[]
    never_recorded: { rec_id: string; name: string }[]
    histogram: number[]
    streamer_hours: { rec_id: string; name: string; hours: number[]; total: number; peak_hour: number }[]
    platform_checks: {
        platform: string
        checks: number
        failures: number
        failure_rate: number
        reasons: FailureReasonCounts
    }[]
    failure_reasons: FailureReasonCounts
    pose: { output_bytes: number; deleted_bytes: number }
    disk: {
        path: string
        total_bytes: number | null
        used_bytes: number | null
        free_bytes: number | null
        recordings_bytes: number
        recordings_files: number
        recordings_video_files: number
        sampled_at: number | null
        cached: boolean
        bytes_per_day: number
        days_left_estimate: number | null
    }
    storage: { total_bytes: number; files: { name: string; bytes: number }[] }
}

export interface MediaItem {
    type: "folder" | "video" | "image"
    name: string
    rel_path: string
    count?: number
    ext?: string
    size?: string
    bytes?: number
    mtime: number
}

export interface MediaTreeResponse {
    path: string
    items: MediaItem[]
}

export interface MediaStats {
    total_files: number
    video_files: number
    total_bytes: number
    total_size: string
    protected_files?: string[]
}

export interface SystemStats {
    total_recordings: number
    active_recordings: number
    monitoring_recordings: number
    stopped_monitoring: number
    recording_enabled: boolean
    storage: MediaStats
}

export interface SystemInfo {
    version: string | null
    kernel_version: string | null
    release_date: string | null
    updates: Record<string, string[]> | null
    announcement: Record<string, Array<{ title: string; content: string }>> | null
    introduction: Record<string, string> | null
    open_source_license: string | null
}

export interface SettingsData {
    /** 有效配置：默认层 ⊕ 用户覆盖层，后端在内存里派生，不落盘 */
    user_settings: Record<string, unknown>
    /** 默认层（镜像内只读模板），用于展示「默认值」和判断是否已覆盖 */
    default_settings: Record<string, unknown>
    /** 稀疏覆盖层：只含用户显式改过的键，落盘的就是这份 */
    user_overrides: Record<string, unknown>
    /** 覆盖层指纹，写入时带回做乐观并发 */
    version: string
    language_code: string
}

export interface SettingsWriteResult {
    ok: boolean
    changed: boolean
    version: string
    user_overrides: Record<string, unknown>
}

export interface SettingsResetResult {
    ok: boolean
    removed: boolean
    /** 恢复默认后该键的有效值 */
    value: unknown
    version: string
    user_overrides: Record<string, unknown>
}

export interface PoseTaskState {
    task_id: string | null
    task_dir: string | null
    status: "running" | "completed" | "failed" | "cancelled"
    state?: string
    pid?: number
    trigger?: string
    video_name?: string
    video_idx?: number
    total_videos?: number
    video_percent?: number
    total_percent?: number
    message?: string
    pending_files?: string[]
    summary?: {
        videos: number
        frames: number
        saved: number
        segments: number
        merged_segments: number
        clips: number
    }
    started_at?: string
    finished_at?: string
    queue_length?: number
    log_size?: number
}
