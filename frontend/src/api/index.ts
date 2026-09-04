import { api } from "./client"
import type { Recording, RecordingStatusesResponse, ValidityCheckResponse } from "./types"

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
    checkValidity: (ids: string[], signal?: AbortSignal) =>
        api.post<ValidityCheckResponse>("/recordings/check-validity", { ids }, signal),
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
    update: (userSettings: Record<string, unknown>) =>
        api.put<{ ok: boolean }>("/settings", { user_settings: userSettings }),
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
