export class ApiError extends Error {
    status: number
    /** 后端错误码（如 err.poseNoVideos）；非码化 detail 为 null */
    code: string | null
    /** 错误码后的附加信息（如文件名列表） */
    params: string | null

    constructor(status: number, message: string) {
        super(message)
        this.status = status
        this.name = "ApiError"
        const m = message.match(/^(err\.[a-zA-Z]+)\|(.*)$/s)
        this.code = m ? m[1] : /^err\.[a-zA-Z]+$/.test(message) ? message : null
        this.params = m ? m[2] : null
    }
}

const DEFAULT_TIMEOUT_MS = 30_000

async function request<T>(path: string, options: RequestInit = {}, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
    // 超时兜底：iOS 切后台/锁屏会把在途 fetch 无限挂起，mutation 的
    // isPending 永远为 true，按钮永久禁用且无提示——必须让挂起变成错误
    const controller = new AbortController()
    const callerSignal = options.signal
    if (callerSignal) {
        if (callerSignal.aborted) controller.abort()
        else callerSignal.addEventListener("abort", () => controller.abort(), { once: true })
    }
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    const init: RequestInit = {
        credentials: "same-origin",
        ...options,
        signal: controller.signal,
        headers: {
            "Content-Type": "application/json",
            ...(options.headers ?? {}),
        },
    }
    try {
        const response = await fetch(`/api${path}`, init)
        if (!response.ok) {
            let detail = response.statusText
            try {
                const body = await response.json()
                detail = body.detail ?? detail
            } catch {
                /* ignore body parse errors */
            }
            throw new ApiError(response.status, detail)
        }
        return (await response.json()) as T
    } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError" && !callerSignal?.aborted) {
            throw new ApiError(408, "err.requestTimeout")
        }
        throw e
    } finally {
        clearTimeout(timer)
    }
}

export const api = {
    get: <T>(path: string) => request<T>(path),
    post: <T>(path: string, body?: unknown, signal?: AbortSignal) =>
        request<T>(path, {
            method: "POST",
            body: body === undefined ? undefined : JSON.stringify(body),
            signal,
        }),
    put: <T>(path: string, body: unknown) =>
        request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
    del: <T>(path: string, search: Record<string, string> = {}) => {
        const qs = new URLSearchParams(search).toString()
        return request<T>(`${path}${qs ? `?${qs}` : ""}`, { method: "DELETE" })
    },
}
