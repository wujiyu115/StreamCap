import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Radio } from "lucide-react"
import { useState } from "react"
import { authApi } from "@/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useI18n } from "@/i18n"
import { toast } from "sonner"

export default function LoginPage() {
    const { t } = useI18n()
    const queryClient = useQueryClient()
    const [username, setUsername] = useState("")
    const [password, setPassword] = useState("")

    const login = useMutation({
        mutationFn: () => authApi.login(username, password),
        onSuccess: async () => {
            toast.success(t("login.success"))
            await queryClient.invalidateQueries({ queryKey: ["session"] })
        },
        onError: () => toast.error(t("login.failed")),
    })

    return (
        <div className="flex h-screen items-center justify-center bg-background">
            <div className="w-full max-w-sm rounded-lg border bg-card p-8 shadow-sm">
                <div className="mb-6 flex flex-col items-center gap-2">
                    <Radio className="h-10 w-10 text-primary" />
                    <h1 className="text-xl font-bold">{t("login.title")}</h1>
                    <p className="text-sm text-muted-foreground">{t("login.subtitle")}</p>
                </div>
                <form
                    className="space-y-4"
                    onSubmit={(e) => {
                        e.preventDefault()
                        if (!username || !password) {
                            toast.error(t("login.required"))
                            return
                        }
                        login.mutate()
                    }}
                >
                    <div className="space-y-2">
                        <Label htmlFor="username">{t("login.username")}</Label>
                        <Input
                            id="username"
                            value={username}
                            onChange={(e) => setUsername(e.target.value)}
                            autoComplete="username"
                        />
                    </div>
                    <div className="space-y-2">
                        <Label htmlFor="password">{t("login.password")}</Label>
                        <Input
                            id="password"
                            type="password"
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            autoComplete="current-password"
                        />
                    </div>
                    <Button type="submit" className="w-full" disabled={login.isPending}>
                        {login.isPending ? t("login.inProgress") : t("login.button")}
                    </Button>
                    <p className="text-center text-xs text-muted-foreground">{t("login.defaultTip")}</p>
                </form>
            </div>
        </div>
    )
}
