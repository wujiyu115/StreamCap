import { ArrowUp } from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { useLocation } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { useI18n } from "@/i18n"
import { cn } from "@/lib/utils"

/** 滚动超过这个距离才显示：手机上要真翻过一屏多才觉得需要它 */
const SHOW_AFTER = 320

/** 这些容器里的滚动属于浮层（弹窗、下拉、日志框），不该触发页面级的「返回顶部」 */
const OVERLAY_SELECTOR =
    '[role="dialog"],[role="listbox"],[role="menu"],[role="combobox"],[data-radix-popper-content-wrapper],pre,textarea'

/**
 * 列表页「返回顶部」悬浮按钮，全站挂一个（挂在 AppLayout 上）。
 *
 * 为什么不绑定某个固定的滚动容器：本项目的滚动区有两套——数据分析等页面整页随
 * <main> 滚动，关注/录制/媒体页则是「头部固定 + 仅列表滚动」的内层容器，关注页
 * 的三个 Tab 还各自是一个滚动容器。所以这里在 document 上挂捕获阶段的 scroll
 * 监听：scroll 事件不冒泡，但捕获阶段能收到任意元素的滚动，谁在滚就听谁。
 *
 * 过滤掉小浮层：弹窗 / 下拉框 / 命令面板里的滚动（含 pose 日志框那种 <pre>）不算
 * 页面滚动，否则在下拉列表里翻两行就会冒出按钮；另外要求容器真能滚出 SHOW_AFTER
 * 以上距离（内容不够长时翻两下就到底，也不需要「回顶」）。
 *
 * 按钮通过 portal 挂到 body：fixed 元素一旦祖先带 transform / filter 就会退化成
 * 相对该祖先定位（Dialog、Tabs 动画都可能加），portal 出去最稳。
 */
export function BackToTop() {
    const { t } = useI18n()
    const { pathname } = useLocation()
    const [visible, setVisible] = useState(false)
    const scrollerRef = useRef<HTMLElement | null>(null)

    // 换页后新页面从顶部开始，按钮不该还挂着
    useEffect(() => {
        scrollerRef.current = null
        setVisible(false)
    }, [pathname])

    useEffect(() => {
        const isPageScroller = (el: HTMLElement) => {
            if (el === document.scrollingElement || el === document.documentElement || el === document.body) return true
            // 弹窗 / 下拉框 / 命令面板 / 日志 <pre> 里的滚动不是页面滚动
            if (el.closest(OVERLAY_SELECTOR)) return false
            // 内容不够长的不算：滚不到 SHOW_AFTER 也就没「回顶」需求
            return el.scrollHeight - el.clientHeight >= SHOW_AFTER
        }

        const onScroll = (event: Event) => {
            const target = event.target
            const el =
                target instanceof HTMLElement
                    ? target
                    : target instanceof Document
                      ? (target.scrollingElement as HTMLElement | null)
                      : null
            if (!el || !isPageScroller(el)) return
            scrollerRef.current = el
            setVisible(el.scrollTop > SHOW_AFTER)
        }

        // 视口变化会改变「半屏高」的判定，重新核一次当前容器
        const recheck = () => {
            const el = scrollerRef.current
            if (!el) return
            setVisible(el.isConnected && el.scrollTop > SHOW_AFTER)
        }

        document.addEventListener("scroll", onScroll, { capture: true, passive: true })
        window.addEventListener("resize", recheck)
        return () => {
            document.removeEventListener("scroll", onScroll, true)
            window.removeEventListener("resize", recheck)
        }
    }, [])

    const backToTop = useCallback(() => {
        const el = scrollerRef.current
        const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false
        const behavior: ScrollBehavior = reduce ? "auto" : "smooth"
        if (el?.isConnected) el.scrollTo({ top: 0, behavior })
        else window.scrollTo({ top: 0, behavior })
    }, [])

    return createPortal(
        <Button
            type="button"
            variant="secondary"
            size="icon"
            data-testid="back-to-top"
            aria-label={t("common.backToTop")}
            aria-hidden={!visible}
            tabIndex={visible ? 0 : -1}
            title={t("common.backToTop")}
            onClick={backToTop}
            className={cn(
                "fixed right-4 z-30 rounded-full border bg-background/95 shadow-lg backdrop-blur transition-all duration-200",
                // 移动端底部导航高 4rem（含 iOS 安全区），按钮落在它上面
                "bottom-[calc(4.5rem+env(safe-area-inset-bottom))] md:right-6 md:bottom-6",
                visible ? "opacity-100" : "pointer-events-none translate-y-2 opacity-0",
            )}
        >
            <ArrowUp className="h-5 w-5" />
        </Button>,
        document.body,
    )
}
