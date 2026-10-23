import { api } from "./client"
import type { AnalyticsOverview, Recording, RecordingStatusesResponse, ValidityCheckResponse } from "./types"

export const analyticsApi = {
    overview: (days: number) => api.get<AnalyticsOverview>(`/analytics/overview?days=${days}`),
}

export const recordingsApi = {
    list: () => api.get<{ recordings: Recording[] }>("/recordings"),
    statuses: () => api.get<RecordingStatusesResponse>("/recordings/statuses"),
    create: (body: Partial<Recording>) => api.post<Recording>("/recordings", body),
    createBatch: (lines: string[]) =>
        api.post<{ created: number; recordings: Recording[] }>("/recordings/batch", { lines }),
    update: (recId: string, body: Partial<Recording>) => api.put<Recording>(`/recordings/${recId}`, body),
    remove: (recId: string) => api.del<{ ok: boolean }>(`/recordings/${recId}`),
    batchDelete: (ids: string[]) =>
        api.post<{ deleted: number; not_found: string[] }>("/recordings/batch-delete", { ids }),
    setMonitor: (recId: string, enabled: boolean) =>
        api.post<Recording>(`/recordings/${recId}/monitor`, { enabled }),
    batchMonitor: (ids: string[], enabled: boolean) =>
        api.post<{ ok: boolean; count: number }>("/recordings/batch-monitor", { ids, enabled }),
    stop: (recId: string) => api.post<Recording>(`/recordings/${recId}/stop`),
    validitySnapshot: () => api.get<ValidityCheckResponse>("/recordings/validity"),
    checkValidity: (
        body: { ids: string[]; force?: boolean; force_since?: number },
        signal?: AbortSignal,
    ) => api.post<ValidityCheckResponse>("/recordings/check-validity", body, signal),
}

export const authApi = {
    session: () =>
        api.get<{ login_required: boolean; authenticated: boolean; username: string | null }>("/auth/session"),
    login: (username: string, password: string) =>
        api.post<{ ok: boolean; username: string }>("/auth/login", { username, password }),
    logout: () => api.post<{ ok: boolean }>("/auth/logout"),
    changePassword: (oldPassword: string, newPassword: string) =>
        api.post<{ ok: boolean }>("/auth/password", {
            old_password: oldPassword,
            new_password: newPassword,
        }),
}

export const settingsApi = {
    get: () => api.get<import("./types").SettingsData>("/settings"),
    /** 只发用户改过的键（构造式稀疏）；version 用于乐观并发，不匹配后端返回 409 */
    update: (patch: Record<string, unknown>, version?: string) =>
        api.put<import("./types").SettingsWriteResult>("/settings", { patch, version }),
    /** 删掉一个覆盖项，让该键恢复跟随默认层。keyPath 支持点号路径 */
    resetKey: (keyPath: string, version?: string) =>
        api.del<import("./types").SettingsResetResult>(
            `/settings/keys/${keyPath}`,
            version ? { version } : {},
        ),
    getCookies: () => api.get<{ cookies: Record<string, string> }>("/settings/cookies"),
    updateCookies: (cookies: Record<string, string>) =>
        api.put<{ ok: boolean }>("/settings/cookies", { cookies }),
    getAccounts: () => api.get<{ accounts: Record<string, Record<string, string>> }>("/settings/accounts"),
    updateAccounts: (accounts: Record<string, Record<string, string>>) =>
        api.put<{ ok: boolean }>("/settings/accounts", { accounts }),
}

export const mediaApi = {
    tree: (path: string) =>
        api.get<import("./types").MediaTreeResponse>(
            `/media/tree${path ? `?path=${encodeURIComponent(path)}` : ""}`,
        ),
    stats: (path = "") =>
        api.get<import("./types").MediaStats>(
            `/media/stats${path ? `?path=${encodeURIComponent(path)}` : ""}`,
        ),
    streamUrl: (relPath: string) => `/api/media/stream?path=${encodeURIComponent(relPath)}`,
    remove: (relPath: string) =>
        api.del<{ ok: boolean }>("/media", { path: relPath }),
    batchDelete: (paths: string[]) =>
        api.post<{ deleted: number; failed: string[] }>("/media/batch-delete", { paths }),
    clean: (path: string, maxBytes: number) =>
        api.post<{ deleted_files: number; deleted_dirs: number; skipped_products: number }>(
            "/media/clean",
            { path, max_bytes: maxBytes },
        ),
}

export const systemApi = {
    info: () => api.get<import("./types").SystemInfo>("/system/info"),
    stats: () => api.get<import("./types").SystemStats>("/system/stats"),
    checkUpdate: () => api.post<Record<string, unknown>>("/system/check-update"),
}

/** 抖音关注列表（我关注的人 / 谁在直播）。refresh 绕过服务端缓存重新拉。 */
export const douyinApi = {
    self: (refresh = false) =>
        api.get<import("./types").DouyinSelfProfile>(`/douyin/following/self${refresh ? "?refresh=true" : ""}`),
    following: (page = 1, pageSize = 20, refresh = false) =>
        api.get<import("./types").DouyinFollowingPage>(
            `/douyin/following?page=${page}&page_size=${pageSize}${refresh ? "&refresh=true" : ""}`,
        ),
    followingLive: (refresh = false) =>
        api.get<import("./types").DouyinLiveResponse>(
            `/douyin/following/live${refresh ? "?refresh=true" : ""}`,
        ),
    liveStream: (webRid: string, quality?: string, refresh = false) =>
        api.get<import("./types").DouyinLiveStream>(
            `/douyin/live/stream?web_rid=${encodeURIComponent(webRid)}` +
                `${quality ? `&quality=${encodeURIComponent(quality)}` : ""}${refresh ? "&refresh=true" : ""}`,
        ),
    // Cookie 寿命（本地解析 sid_guard）：设置页/关注页用来提前提醒续期
    cookieStatus: () => api.get<import("./types").DouyinCookieStatus>("/douyin/cookie"),
    // 扫码登录：start 幂等（已在跑就返回当前状态），status 轮询到 confirmed 时后端顺手把 Cookie 写进设置
    loginStart: () => api.post<import("./types").DouyinLoginState>("/douyin/login"),
    loginStatus: () => api.get<import("./types").DouyinLoginState>("/douyin/login"),
    loginCancel: () => api.del<import("./types").DouyinLoginState>("/douyin/login"),
}

export const bilibiliApi = {
    loginStart: () => api.post<import("./types").BilibiliLoginState>("/bilibili/login"),
    loginStatus: () => api.get<import("./types").BilibiliLoginState>("/bilibili/login"),
    loginCancel: () => api.del<import("./types").BilibiliLoginState>("/bilibili/login"),
}

export const uploadsApi = {
    list: () => api.get<import("./types").UploadsResponse>("/uploads"),
    retry: (jobId: string) => api.post<{ ok: boolean }>(`/uploads/${jobId}/retry`),
    resumeChannel: (provider: string) =>
        api.post<{ ok: boolean }>(`/uploads/channels/${provider}/resume`),
}

export const poseApi = {
    submit: (paths: string[], trigger = "manual", overrides?: Record<string, unknown>) =>
        api.post<{ task_id: string | null; status: string }>("/pose/tasks", {
            paths,
            trigger,
            overrides: overrides ?? null,
        }),
    list: () => api.get<{ tasks: import("./types").PoseTaskState[] }>("/pose/tasks"),
    stop: (taskId: string) => api.post<import("./types").PoseTaskState>(`/pose/tasks/${taskId}/stop`),
    log: (taskId: string, offset = 0) =>
        api.get<{ chunk: string; next_offset: number }>(
            `/pose/tasks/${taskId}/log?offset=${offset}`,
        ),
}
