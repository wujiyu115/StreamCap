import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Home, Info, LogOut, Menu, Moon, Radio, Settings, Sun, Video } from "lucide-react"
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
import { I18nContext, format, getDict, resolveDictValue, setCurrentLang, type LangCode } from "@/i18n"

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

    // 同步模块级语言，供非 hook 场景（toast 错误翻译）使用
    useEffect(() => {
        setCurrentLang(lang)
    }, [lang])

    const dict = getDict(lang)

    return {
        lang,
        setLang,
        t: (path: string) => resolveDictValue(dict, path),
        tf: (path: string, params: Record<string, string | number>) =>
            format(resolveDictValue(dict, path), params),
    }
}

function useTheme() {
    const [dark, setDark] = useState(() => localStorage.getItem(THEME_KEY) === "dark")
    useEffect(() => {
        document.documentElement.classList.toggle("dark", dark)
    }, [dark])
    return { dark, toggle: () => setDark((d) => !d) }
}

const NAV_ITEMS = [
    { to: "/home", icon: Home, labelKey: "nav.home" },
    { to: "/recordings", icon: Radio, labelKey: "nav.recordings" },
    { to: "/media", icon: Video, labelKey: "nav.media" },
    { to: "/settings", icon: Settings, labelKey: "nav.settings" },
    { to: "/about", icon: Info, labelKey: "nav.about" },
]

/** 桌面：固定左侧边栏；移动（<md）：顶栏 + 底部 Tab 导航 */
export function AppLayout({ children }: { children: React.ReactNode }) {
    const i18n = useI18nState()
    const theme = useTheme()
    const queryClient = useQueryClient()
    const { t } = i18n

    const logout = useMutation({
        mutationFn: authApi.logout,
        onSuccess: () => {
            queryClient.clear()
            window.location.reload()
        },
    })

    return (
        <I18nContext.Provider value={i18n}>
            <div className="flex h-dvh flex-col overflow-hidden bg-background md:flex-row">
                {/* 移动端顶栏 */}
                <header className="flex h-12 shrink-0 items-center justify-between border-b bg-sidebar px-3 md:hidden">
                    <div className="flex items-center gap-2">
                        <Radio className="h-5 w-5 text-primary" />
                        <span className="font-bold">StreamCap</span>
                    </div>
                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon" className="h-8 w-8">
                                <Menu className="h-5 w-5" />
                            </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                            <DropdownMenuItem onClick={() => i18n.setLang("zh_CN")}>
                                简体中文 {i18n.lang === "zh_CN" && "✓"}
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => i18n.setLang("en")}>
                                English {i18n.lang === "en" && "✓"}
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={theme.toggle}>
                                {theme.dark ? (
                                    <Sun className="mr-2 h-4 w-4" />
                                ) : (
                                    <Moon className="mr-2 h-4 w-4" />
                                )}
                                {t("common.theme")}
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => logout.mutate()}>
                                <LogOut className="mr-2 h-4 w-4" />
                                {t("common.logout")}
                            </DropdownMenuItem>
                        </DropdownMenuContent>
                    </DropdownMenu>
                </header>

                {/* 桌面侧边栏 */}
                <aside className="hidden w-56 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground md:flex">
                    <div className="flex h-14 items-center gap-2 border-b px-4">
                        <Radio className="h-6 w-6 text-primary" />
                        <span className="text-lg font-bold">StreamCap</span>
                    </div>
                    <nav className="flex-1 space-y-1 p-2">
                        {NAV_ITEMS.map((item) => (
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
                                {t(item.labelKey)}
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
                            onClick={theme.toggle}
                        >
                            {theme.dark ? "☀️" : "🌙"} {t("common.theme")}
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

                {/* 主内容区：移动端留出底部导航高度；flex 让页面可实现
                    「头部固定 + 仅列表滚动」布局 */}
                <main className="min-w-0 flex-1 overflow-auto pb-16 md:pb-0">
                    <div className="mx-auto flex h-full max-w-7xl flex-col p-4 md:p-6">{children}</div>
                </main>

                {/* 移动端底部导航 */}
                <nav className="fixed inset-x-0 bottom-0 z-10 flex h-16 items-stretch border-t bg-sidebar md:hidden">
                    {NAV_ITEMS.map((item) => (
                        <NavLink
                            key={item.to}
                            to={item.to}
                            className={({ isActive }) =>
                                `flex flex-1 flex-col items-center justify-center gap-1 text-[11px] transition-colors ${
                                    isActive ? "text-primary" : "text-muted-foreground"
                                }`
                            }
                        >
                            <item.icon className="h-5 w-5" />
                            <span className="truncate">{t(item.labelKey)}</span>
                        </NavLink>
                    ))}
                </nav>
            </div>
        </I18nContext.Provider>
    )
}
