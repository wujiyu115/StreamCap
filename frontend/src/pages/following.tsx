import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
    ExternalLink,
    Loader2,
    Play,
    Plus,
    QrCode,
    Radio,
    RefreshCw,
    UserMinus,
    Users,
} from "lucide-react"
import { useMemo, useState } from "react"
import { Link } from "react-router-dom"
import { douyinApi, settingsApi } from "@/api"
import { ApiError } from "@/api/client"
import type { DouyinFollowingPage, DouyinFollowingUser, DouyinLiveRoom } from "@/api/types"
import { useConfirm } from "@/components/confirm-dialog"
import { DouyinLoginDialog } from "@/components/douyin-login-dialog"
import { LivePlayerDialog } from "@/components/live-player-dialog"
import { RecordingDialog } from "@/components/recording-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { formatDate, translateError, useI18n } from "@/i18n"
import { toast } from "sonner"

const PAGE_SIZES = [20, 50, 100]

const queryKeys = {
    self: ["douyin-self"] as const,
    live: ["douyin-live"] as const,
    following: (page: number, pageSize: number) => ["douyin-following", page, pageSize] as const,
}

function errorText(error: unknown): string | null {
    if (!error) return null
    if (error instanceof Error) return translateError(error.message)
    return String(error)
}

function isLoginExpired(error: unknown): boolean {
    return error instanceof ApiError && error.code === "err.douyinLoginRequired"
}

function isCookieMissing(error: unknown): boolean {
    return error instanceof ApiError && error.code === "err.douyinCookieMissing"
}

/** 大数字缩写：1.2万 */
function compact(count: number | null | undefined): string {
    if (count == null) return "-"
    if (count >= 10000) return `${(count / 10000).toFixed(1)}万`
    return String(count)
}

function Avatar({ src, className }: { src: string; className: string }) {
    if (!src) {
        return <div className={`shrink-0 rounded-full bg-muted ${className}`} />
    }
    return (
        <img
            src={src}
            alt=""
            referrerPolicy="no-referrer"
            loading="lazy"
            className={`shrink-0 rounded-full bg-muted object-cover ${className}`}
        />
    )
}

function LiveBadge() {
    const { t } = useI18n()
    return (
        <Badge className="shrink-0 bg-red-500 text-white hover:bg-red-500">
            <Radio className="mr-1 h-3 w-3" />
            {t("following.liveNow")}
        </Badge>
    )
}

/** 关注的人：头像 / 昵称 / 粉丝数 / 在播徽标 / 一键加监控 / 去抖音取关 */
function UserRow({
    user,
    room,
    onMonitor,
    onUnfollow,
}: {
    user: DouyinFollowingUser
    room?: DouyinLiveRoom
    onMonitor: (room: DouyinLiveRoom) => void
    onUnfollow: (user: DouyinFollowingUser) => void
}) {
    const { t, tf } = useI18n()
    const isLive = Boolean(room)
    // 没在播的也能加监控（房间地址用短号，没有短号回落到抖音号）；
    // 实测 live.douyin.com/{short_id} 能解析回同一个主播，可交给监控等开播
    const roomId = user.short_id || user.unique_id
    const monitorRoom: DouyinLiveRoom | undefined =
        room ??
        (roomId
            ? {
                  web_rid: roomId,
                  room_id: user.room_id,
                  title: "",
                  cover: "",
                  online: "",
                  tag: null,
                  status: 0,
                  anchor: {
                      sec_uid: user.sec_uid,
                      nickname: user.nickname,
                      avatar: user.avatar,
                      web_rid: roomId,
                  },
              }
            : undefined)
    return (
        <div className="flex items-center gap-3 rounded-lg border p-3">
            <Avatar src={user.avatar} className="h-10 w-10" />
            <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium">{user.nickname || user.sec_uid}</span>
                    {user.verified && (
                        <Badge variant="secondary" className="shrink-0">
                            {user.custom_verify || t("following.verified")}
                        </Badge>
                    )}
                    {isLive && <LiveBadge />}
                </div>
                <div className="truncate text-xs text-muted-foreground">
                    {user.unique_id ? `@${user.unique_id}` : user.sec_uid} ·{" "}
                    {tf("following.followers", { count: compact(user.follower_count) })}
                    {user.aweme_count ? ` · ${tf("following.works", { count: compact(user.aweme_count) })}` : ""}
                </div>
                {user.signature && (
                    <div className="truncate text-xs text-muted-foreground">{user.signature}</div>
                )}
            </div>
            {monitorRoom && (
                <Button size="sm" variant="outline" className="shrink-0" onClick={() => onMonitor(monitorRoom)}>
                    <Plus className="mr-1 h-3.5 w-3.5" />
                    {t("following.monitor")}
                </Button>
            )}
            <a
                href={`https://www.douyin.com/user/${user.sec_uid}`}
                target="_blank"
                rel="noreferrer"
                title={t("following.unfollow")}
                data-testid={`unfollow-${user.sec_uid}`}
                className="shrink-0 text-muted-foreground hover:text-destructive"
                onClick={(e) => {
                    // 中键/⌘ 点击照旧走原生行为，左键走带说明的确认框
                    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return
                    e.preventDefault()
                    onUnfollow(user)
                }}
            >
                <UserMinus className="h-4 w-4" />
            </a>
        </div>
    )
}

/** 正在直播的关注用户卡片 */
function LiveRoomCard({
    room,
    onMonitor,
    onPlay,
}: {
    room: DouyinLiveRoom
    onMonitor: (room: DouyinLiveRoom) => void
    onPlay: (room: DouyinLiveRoom) => void
}) {
    const { t, tf } = useI18n()
    return (
        <Card>
            <CardContent className="flex gap-3 p-3">
                <a
                    href={`https://live.douyin.com/${room.web_rid}`}
                    target="_blank"
                    rel="noreferrer"
                    className="relative h-20 w-32 shrink-0 overflow-hidden rounded-md bg-muted"
                >
                    {room.cover ? (
                        <img
                            src={room.cover}
                            alt=""
                            referrerPolicy="no-referrer"
                            loading="lazy"
                            className="h-full w-full object-cover"
                        />
                    ) : null}
                    <span className="absolute bottom-1 right-1 rounded bg-black/60 px-1 text-[10px] text-white">
                        {room.online ? tf("following.online", { count: room.online }) : t("following.liveNow")}
                    </span>
                </a>
                <div className="min-w-0 flex-1">
                    <a
                        href={`https://live.douyin.com/${room.web_rid}`}
                        target="_blank"
                        rel="noreferrer"
                        className="line-clamp-2 text-sm font-medium hover:underline"
                    >
                        {room.title || room.anchor.nickname || room.web_rid}
                    </a>
                    <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                        <Avatar src={room.anchor.avatar} className="h-5 w-5" />
                        <span className="truncate">{room.anchor.nickname || room.web_rid}</span>
                        {room.tag && (
                            <Badge variant="secondary" className="shrink-0">
                                {room.tag}
                            </Badge>
                        )}
                    </div>
                    <div className="mt-2 flex flex-wrap gap-2">
                        <Button size="sm" onClick={() => onPlay(room)}>
                            <Play className="mr-1 h-3.5 w-3.5" />
                            {t("following.play")}
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => onMonitor(room)}>
                            <Plus className="mr-1 h-3.5 w-3.5" />
                            {t("following.monitor")}
                        </Button>
                        <Button size="sm" variant="outline" asChild>
                            <a href={`https://live.douyin.com/${room.web_rid}`} target="_blank" rel="noreferrer">
                                <ExternalLink className="mr-1 h-3.5 w-3.5" />
                                {t("following.openRoom")}
                            </a>
                        </Button>
                    </div>
                </div>
            </CardContent>
        </Card>
    )
}

function ErrorNotice({ text, action }: { text: string; action?: React.ReactNode }) {
    return (
        <div className="flex flex-wrap items-start justify-between gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
            <span className="flex items-start gap-2">
                <span>⚠️</span>
                <span>{text}</span>
            </span>
            {action}
        </div>
    )
}

function CookieNotice({ text, action }: { text: string; action?: React.ReactNode }) {
    const { t } = useI18n()
    return (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
            <span>{text}</span>
            <div className="flex items-center gap-2">
                {action}
                <Button size="sm" variant="outline" asChild>
                    <Link to="/settings">{t("following.goSettings")}</Link>
                </Button>
            </div>
        </div>
    )
}

export default function FollowingPage() {
    const { t, tf, lang } = useI18n()
    const queryClient = useQueryClient()
    const [tab, setTab] = useState("all")
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = useState(20)
    const [preset, setPreset] = useState<{ url: string; streamer_name: string } | null>(null)
    const [dialogOpen, setDialogOpen] = useState(false)
    const [playerRoom, setPlayerRoom] = useState<DouyinLiveRoom | null>(null)
    const [loginOpen, setLoginOpen] = useState(false)
    const [refreshing, setRefreshing] = useState(false)
    const confirmBox = useConfirm()

    // Cookie 是否已配置（没配就不用打接口了，直接给提示）
    const cookies = useQuery({
        queryKey: ["cookies"],
        queryFn: settingsApi.getCookies,
        staleTime: 30_000,
    })
    const cookieReady = cookies.isSuccess
    const hasCookie = Boolean(cookies.data?.cookies?.douyin?.trim())

    // 到期提醒：后端只解析本地 sid_guard（不出网），只用来提前提醒续期；
    // 「已失效」有接口错误码兑底，这里不重复报
    const cookieStatus = useQuery({
        queryKey: ["douyin-cookie"],
        queryFn: douyinApi.cookieStatus,
        enabled: hasCookie,
        staleTime: 300_000,
    })
    const expiry = cookieStatus.data
    const expiryDate = formatDate(expiry?.expires_at, lang)
    const expiryDays = Math.max(1, Math.ceil(expiry?.days_left ?? 0))

    const profile = useQuery({
        queryKey: queryKeys.self,
        queryFn: () => douyinApi.self(),
        enabled: hasCookie,
        retry: false,
        staleTime: 300_000,
    })
    const live = useQuery({
        queryKey: queryKeys.live,
        queryFn: () => douyinApi.followingLive(),
        enabled: hasCookie,
        retry: false,
        refetchInterval: 60_000,
        refetchIntervalInBackground: false,
    })
    const following = useQuery({
        queryKey: queryKeys.following(page, pageSize),
        queryFn: () => douyinApi.following(page, pageSize),
        enabled: hasCookie,
        retry: false,
    })

    // 在播房间索引：优先按 sec_uid 配对，退化到 short_id == web_rid
    const liveRooms = live.data?.rooms ?? []
    const liveBySecUid = useMemo(() => {
        const map = new Map<string, DouyinLiveRoom>()
        for (const room of liveRooms) {
            if (room.anchor.sec_uid && !map.has(room.anchor.sec_uid)) map.set(room.anchor.sec_uid, room)
        }
        return map
    }, [liveRooms])
    const liveByShortId = useMemo(() => {
        const map = new Map<string, DouyinLiveRoom>()
        for (const room of liveRooms) {
            if (room.web_rid && !map.has(room.web_rid)) map.set(room.web_rid, room)
            if (room.anchor.web_rid && !map.has(room.anchor.web_rid)) map.set(room.anchor.web_rid, room)
        }
        return map
    }, [liveRooms])

    const errors = [profile.error, live.error, following.error]
    const loginExpired = errors.some(isLoginExpired)
    const cookieMissing = errors.some(isCookieMissing)
    const genericError = errors.find((e) => e && !isLoginExpired(e) && !isCookieMissing(e))

    const openMonitor = (room: DouyinLiveRoom) => {
        setPreset({
            url: `https://live.douyin.com/${room.web_rid}`,
            streamer_name: room.anchor.nickname || room.web_rid,
        })
        setDialogOpen(true)
    }

    // 取消关注：抖音从 2026-08 起给行为接口（关注/取关、点赞…）加了站点侧签名
    // （x-secsdk-web-signature / ArgusSecurityPlugin），服务端复现不出来——实测同一份有效
    // Cookie 打读接口 200、打 commit/follow 一律 403，注入 Cookie 的 headless 浏览器也被
    // 判成「未登录」。所以这里不假装能取关：确认后把人送到抖音主页，由用户在那里点。
    const askUnfollow = async (target: DouyinFollowingUser) => {
        const name = target.nickname || target.unique_id || target.sec_uid
        const ok = await confirmBox({
            description: tf("following.unfollowHint", { name }),
            confirmText: t("following.openProfile"),
        })
        if (ok) window.open(`https://www.douyin.com/user/${target.sec_uid}`, "_blank", "noopener,noreferrer")
    }

    // Cookie 失效时的「扫码登录」入口：不用再手动去浏览器里导 Cookie
    const scanLoginButton = (
        <Button
            size="sm"
            variant="outline"
            className="shrink-0"
            data-testid="douyin-scan-login"
            onClick={() => setLoginOpen(true)}
        >
            <QrCode className="mr-1 h-4 w-4" />
            {t("following.scanLogin")}
        </Button>
    )

    const refreshAll = async () => {
        setRefreshing(true)
        try {
            const [liveData, pageData, selfData] = await Promise.all([
                douyinApi.followingLive(true),
                douyinApi.following(page, pageSize, true),
                douyinApi.self(true),
            ])
            queryClient.setQueryData(queryKeys.live, liveData)
            queryClient.setQueryData(queryKeys.following(page, pageSize), pageData)
            queryClient.setQueryData(queryKeys.self, selfData)
            toast.success(t("common.success"))
        } catch (e) {
            toast.error(errorText(e) ?? t("common.failed"))
        } finally {
            setRefreshing(false)
        }
    }

    const userRow = (user: DouyinFollowingUser) => (
        <UserRow
            key={user.sec_uid}
            user={user}
            room={liveBySecUid.get(user.sec_uid) ?? (user.short_id ? liveByShortId.get(user.short_id) : undefined)}
            onMonitor={openMonitor}
            onUnfollow={askUnfollow}
        />
    )

    const total = following.data?.total
    const maxPage = following.data?.max_page ?? page
    // 接口只报 total 与已取页数，真实总页数由前端算（total=429、每页 20 → 共 22 页）
    const totalPages = total != null && total > 0 ? Math.max(1, Math.ceil(total / pageSize)) : undefined

    return (
        <div className="flex h-full flex-col gap-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                    <Avatar src={profile.data?.avatar ?? ""} className="h-10 w-10" />
                    <div>
                        <h1 className="text-xl font-bold">{t("following.title")}</h1>
                        <p className="text-xs text-muted-foreground">
                            {profile.data
                                ? `${profile.data.nickname} · ${
                                      total != null
                                          ? tf("following.totalFollowing", { count: compact(total) })
                                          : tf("following.myFollowing", {
                                                count: compact(profile.data.following_count),
                                            })
                                  }`
                                : t("following.subtitle")}
                        </p>
                    </div>
                </div>
                <Button variant="outline" size="sm" onClick={refreshAll} disabled={!hasCookie || refreshing}>
                    {refreshing ? (
                        <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                    ) : (
                        <RefreshCw className="mr-1 h-4 w-4" />
                    )}
                    {t("common.refresh")}
                </Button>
            </div>

            {cookieReady && !hasCookie && (
                <CookieNotice text={t("following.cookieMissing")} action={scanLoginButton} />
            )}
            {cookieMissing && <CookieNotice text={t("following.cookieMissing")} action={scanLoginButton} />}
            {expiry?.state === "expiring" && (
                <CookieNotice
                    text={tf("following.cookieExpiring", { date: expiryDate, days: expiryDays })}
                    action={scanLoginButton}
                />
            )}
            {loginExpired && (
                <ErrorNotice text={t("following.loginExpired")} action={scanLoginButton} />
            )}
            {genericError && <ErrorNotice text={errorText(genericError) ?? t("common.failed")} />}

            <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col">
                <TabsList className="w-fit">
                    <TabsTrigger value="all">
                        <Users className="mr-1 h-4 w-4" />
                        {t("following.tabAll")}
                    </TabsTrigger>
                    <TabsTrigger value="live">
                        <Radio className="mr-1 h-4 w-4" />
                        {t("following.tabLive")}
                        {liveRooms.length > 0 && (
                            <Badge variant="secondary" className="ml-1">
                                {liveRooms.length}
                            </Badge>
                        )}
                    </TabsTrigger>
                </TabsList>

                <TabsContent value="all" className="mt-3 flex min-h-0 flex-1 flex-col data-[state=inactive]:hidden">
                    <div className="min-h-0 flex-1 space-y-2 overflow-auto">
                        {following.isLoading &&
                            Array.from({ length: 5 }).map((_, i) => (
                                <Skeleton key={i} className="h-16 w-full rounded-lg" />
                            ))}
                        {following.data?.users.map((user) => userRow(user))}
                        {!following.isLoading && (following.data?.users.length ?? 0) === 0 && (
                            <div className="py-10 text-center text-sm text-muted-foreground">
                                {t("following.emptyAll")}
                            </div>
                        )}
                    </div>

                    <div className="flex flex-wrap items-center justify-between gap-2 pt-3">
                        <Select
                            value={String(pageSize)}
                            onValueChange={(v) => {
                                setPageSize(Number(v))
                                setPage(1)
                            }}
                        >
                            <SelectTrigger className="w-28">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                {PAGE_SIZES.map((n) => (
                                    <SelectItem key={n} value={String(n)}>
                                        {tf("following.pageSize", { count: n })}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <div className="flex items-center gap-2">
                            <Button
                                variant="outline"
                                size="sm"
                                disabled={page <= 1 || following.isFetching}
                                onClick={() => setPage((p) => Math.max(1, p - 1))}
                            >
                                {t("following.prevPage")}
                            </Button>
                            <span className="whitespace-nowrap text-xs text-muted-foreground">
                                {totalPages != null
                                    ? tf("following.pageOfTotal", { page, max: totalPages })
                                    : tf("following.pageOf", { page, max: maxPage })}
                            </span>
                            <Button
                                variant="outline"
                                size="sm"
                                disabled={
                                    !following.data?.has_more ||
                                    following.isFetching ||
                                    (totalPages != null && page >= totalPages)
                                }
                                onClick={() => setPage((p) => p + 1)}
                            >
                                {following.isFetching ? (
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                ) : (
                                    t("following.nextPage")
                                )}
                            </Button>
                        </div>
                    </div>
                </TabsContent>

                <TabsContent value="live" className="mt-3 min-h-0 flex-1 overflow-auto data-[state=inactive]:hidden">
                    {live.isLoading && (
                        <div className="grid gap-3 md:grid-cols-2">
                            {Array.from({ length: 4 }).map((_, i) => (
                                <Skeleton key={i} className="h-24 w-full rounded-lg" />
                            ))}
                        </div>
                    )}
                    {(live.data?.rooms.length ?? 0) > 0 && (
                        <div className="grid gap-3 md:grid-cols-2">
                            {live.data?.rooms.map((room) => (
                                <LiveRoomCard
                                    key={`${room.web_rid}-${room.room_id}`}
                                    room={room}
                                    onMonitor={openMonitor}
                                    onPlay={setPlayerRoom}
                                />
                            ))}
                        </div>
                    )}
                    {!live.isLoading && (live.data?.rooms.length ?? 0) === 0 && (
                        <div className="py-10 text-center text-sm text-muted-foreground">
                            {t("following.emptyLive")}
                        </div>
                    )}
                </TabsContent>
            </Tabs>

            <LivePlayerDialog
                room={playerRoom}
                onOpenChange={(open) => {
                    if (!open) setPlayerRoom(null)
                }}
            />

            <DouyinLoginDialog
                open={loginOpen}
                onOpenChange={setLoginOpen}
                onSuccess={() => {
                    // 新 Cookie 到了：关注 / 在播 / 自己都得重拉（旧缓存是按旧 Cookie 绑的）
                    void queryClient.invalidateQueries({ queryKey: queryKeys.self })
                    void queryClient.invalidateQueries({ queryKey: queryKeys.live })
                    void queryClient.invalidateQueries({ queryKey: ["douyin-following"] })
                }}
            />

            <RecordingDialog
                open={dialogOpen}
                recording={null}
                preset={preset}
                onOpenChange={(open) => {
                    setDialogOpen(open)
                    if (!open) setPreset(null)
                }}
                onSaved={() => {
                    queryClient.invalidateQueries({ queryKey: ["recordings"] })
                }}
            />
        </div>
    )
}