import { useQuery } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
import { createBrowserRouter, Navigate, Outlet } from "react-router-dom"
import { authApi } from "@/api"
import { AppLayout } from "@/layouts/app-layout"
import { AuthGate } from "@/layouts/auth-gate"
import AboutPage from "@/pages/about"
import HomePage from "@/pages/home"
import LoginPage from "@/pages/login"
import MediaPage from "@/pages/media"
import RecordingsPage from "@/pages/recordings"
import SettingsPage from "@/pages/settings"

function Pending({ label = "Loading" }: { label?: string }) {
    return (
        <div className="flex h-screen items-center justify-center gap-2 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
            {label}...
        </div>
    )
}

function RootLayout() {
    const { data, isLoading } = useQuery({
        queryKey: ["session"],
        queryFn: authApi.session,
        staleTime: 60_000,
    })

    if (isLoading) return <Pending />
    if (!data) return <LoginPage />

    if (data.login_required && !data.authenticated) {
        return (
            <AuthGate>
                <LoginPage />
            </AuthGate>
        )
    }

    return (
        <AppLayout>
            <Outlet />
        </AppLayout>
    )
}

export const router = createBrowserRouter([
    {
        element: <RootLayout />,
        children: [
            { path: "/", element: <Navigate to="/home" replace /> },
            { path: "/home", element: <HomePage /> },
            { path: "/recordings", element: <RecordingsPage /> },
            { path: "/media", element: <MediaPage /> },
            { path: "/settings", element: <SettingsPage /> },
            { path: "/about", element: <AboutPage /> },
        ],
    },
])
