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

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const init: RequestInit = {
        credentials: "same-origin",
        ...options,
        headers: {
            "Content-Type": "application/json",
            ...(options.headers ?? {}),
        },
    }
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
}

export const api = {
    get: <T>(path: string) => request<T>(path),
    post: <T>(path: string, body?: unknown) =>
        request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
    put: <T>(path: string, body: unknown) =>
        request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
    del: <T>(path: string, search: Record<string, string> = {}) => {
        const qs = new URLSearchParams(search).toString()
        return request<T>(`${path}${qs ? `?${qs}` : ""}`, { method: "DELETE" })
    },
}
