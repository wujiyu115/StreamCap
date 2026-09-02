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

export function getDict(lang: LangCode): Dict {
    return DICTS[lang] ?? DICTS.zh_CN
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
