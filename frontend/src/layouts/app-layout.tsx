import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Home, Info, Radio, Settings, Video } from "lucide-react"
import { useEffect, useState } from "react"
import { NavLink } from "react-router-dom"
import { authApi } from "@/api"
import { Button } from "@/components/ui/button"
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { I18nContext, format, getDict, resolveDictValue, type LangCode } from "@/i18n"

const LANG_KEY = "streamcap.lang"
const THEME_KEY = "streamcap.theme"

function useI18nState() {
    const [lang, setLangState] = useState<LangCode>(
        () => (localStorage.getItem(LANG_KEY) as LangCode) ?? "zh_CN",
    )

    const setLang = (next: LangCode) => {
        setLangState(next)
        localStorage.setItem(LANG_KEY, next)
    }

    const dict = getDict(lang)

    return {
        lang,
        setLang,
        t: (path: string) => resolveDictValue(dict, path),
        tf: (path: string, params: Record<string, string | number>) =>
            format(resolveDictValue(dict, path), params),
    }
}

export function AppLayout({ children }: { children: React.ReactNode }) {
    const i18n = useI18nState()
    const [dark, setDark] = useState(() => localStorage.getItem(THEME_KEY) === "dark")
    const queryClient = useQueryClient()
    const { t } = i18n

    useEffect(() => {
        document.documentElement.classList.toggle("dark", dark)
    }, [dark])

    const logout = useMutation({
        mutationFn: authApi.logout,
        onSuccess: () => {
            queryClient.clear()
            window.location.reload()
        },
    })

    const navItems = [
        { to: "/home", icon: Home, label: t("nav.home") },
        { to: "/recordings", icon: Radio, label: t("nav.recordings") },
        { to: "/media", icon: Video, label: t("nav.media") },
        { to: "/settings", icon: Settings, label: t("nav.settings") },
        { to: "/about", icon: Info, label: t("nav.about") },
    ]

    return (
        <I18nContext.Provider value={i18n}>
            <div className="flex h-screen overflow-hidden bg-background">
                <aside className="flex w-56 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground">
                    <div className="flex h-14 items-center gap-2 border-b px-4">
                        <Radio className="h-6 w-6 text-primary" />
                        <span className="text-lg font-bold">StreamCap</span>
                    </div>
                    <nav className="flex-1 space-y-1 p-2">
                        {navItems.map((item) => (
                            <NavLink
                                key={item.to}
                                to={item.to}
                                className={({ isActive }) =>
                                    `flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${
                                        isActive
                                            ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                                            : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground"
                                    }`
                                }
                            >
                                <item.icon className="h-4 w-4" />
                                {item.label}
                            </NavLink>
                        ))}
                    </nav>
                    <div className="space-y-1 border-t p-3">
                        <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                                <Button variant="outline" size="sm" className="w-full justify-start gap-2">
                                    🌐 {t("common.language")}
                                </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="start">
                                <DropdownMenuItem onClick={() => i18n.setLang("zh_CN")}>
                                    简体中文 {i18n.lang === "zh_CN" && "✓"}
                                </DropdownMenuItem>
                                <DropdownMenuItem onClick={() => i18n.setLang("en")}>
                                    English {i18n.lang === "en" && "✓"}
                                </DropdownMenuItem>
                            </DropdownMenuContent>
                        </DropdownMenu>
                        <Button
                            variant="ghost"
                            size="sm"
                            className="w-full justify-start gap-2 text-muted-foreground"
                            onClick={() => setDark((d) => !d)}
                        >
                            {dark ? "☀️" : "🌙"} {t("common.theme")}
                        </Button>
                        <Button
                            variant="ghost"
                            size="sm"
                            className="w-full justify-start gap-2 text-muted-foreground"
                            onClick={() => logout.mutate()}
                        >
                            🚪 {t("common.logout")}
                        </Button>
                    </div>
                </aside>
                <main className="flex-1 overflow-auto">
                    <div className="mx-auto max-w-7xl p-6">{children}</div>
                </main>
            </div>
        </I18nContext.Provider>
    )
}
