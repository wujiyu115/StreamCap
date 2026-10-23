import { createContext, useContext } from "react"
import zhCN from "./zh_CN.json"
import en from "./en.json"

export type Dict = typeof zhCN
export type LangCode = "zh_CN" | "en"

const DICTS: Record<LangCode, Dict> = {
    zh_CN: zhCN,
    en: en as unknown as Dict,
}

export const SUPPORTED_LANGUAGES: Array<{ code: LangCode; label: string }> = [
    { code: "zh_CN", label: "简体中文" },
    { code: "en", label: "English" },
]

/** 当前语言（模块级，供非 hook 场景如 toast 错误翻译使用） */
let currentLang: LangCode = "zh_CN"

export function getDict(lang: LangCode): Dict {
    return DICTS[lang] ?? DICTS.zh_CN
}

export function setCurrentLang(lang: LangCode): void {
    currentLang = lang
}

/** 翻译 "err.xxx|附加信息" 格式的 API 错误：码查 i18n，附加信息原样拼回 */
export function translateError(message: string): string {
    const m = message.match(/^(err\.[a-zA-Z]+)(?:\|(.*))?$/s)
    if (!m) return message
    const template = resolveDictValue(DICTS[currentLang] ?? DICTS.zh_CN, m[1])
    const text = template === m[1] ? m[1] : template
    return m[2] ? `${text}：${m[2]}` : text
}

export function format(template: string, params: Record<string, string | number>): string {
    return template.replace(/\{(\w+)\}/g, (_, key: string) =>
        key in params ? String(params[key]) : `{${key}}`,
    )
}

export interface I18nContextValue {
    lang: LangCode
    setLang: (lang: LangCode) => void
    t: (path: string) => string
    tf: (path: string, params: Record<string, string | number>) => string
}

export const I18nContext = createContext<I18nContextValue>({
    lang: "zh_CN",
    setLang: () => undefined,
    t: (path) => path,
    tf: (path) => path,
})

export function useI18n(): I18nContextValue {
    return useContext(I18nContext)
}

/** 按界面语言格式化日期（入参是 epoch 秒）。
 *
 * 默认的 toLocaleDateString() 跟随**浏览器**语言，中文界面会被渲染成
 * 12/20/2026；这里显式按 i18n 语言来，跟界面其余部分保持一致。
 */
export function formatDate(epochSeconds: number | null | undefined, lang: LangCode = currentLang): string {
    if (!epochSeconds) return "-"
    return new Date(epochSeconds * 1000).toLocaleDateString(lang === "en" ? "en-US" : "zh-CN")
}

/** 按界面语言格式化时间串（入参是后端写库的 ISO 本地时间，如 2026-10-23T22:36:42）。
 *
 * 只显示到分钟，够用且不会把上传记录那行撑得太长（手机上尤其明显）。
 */
export function formatDateTime(value: string | null | undefined, lang: LangCode = currentLang): string {
    if (!value) return "-"
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return value
    const pad = (n: number) => String(n).padStart(2, "0")
    const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
    const time = `${pad(date.getHours())}:${pad(date.getMinutes())}`
    // 英文界面按 en-US 习惯用月/日，中文用年-月-日
    return lang === "en" ? `${date.getMonth() + 1}/${date.getDate()}/${date.getFullYear()} ${time}` : `${day} ${time}`
}

export function resolveDictValue(dict: Dict, path: string): string {
    const parts = path.split(".")
    let current: unknown = dict
    for (const part of parts) {
        if (current == null || typeof current !== "object") {
            return path
        }
        current = (current as Record<string, unknown>)[part]
    }
    return typeof current === "string" ? current : path
}
