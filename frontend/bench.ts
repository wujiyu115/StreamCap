// StreamCap 前端基准：构建产物体积与关键组件渲染性能。
// 运行：cd frontend && bun run bench
// 依赖 bench 数据的组件渲染需 vitest 环境，这里用轻量方案：
// 1) 产物体积基准（build 后解析 dist/assets）
// 2) 状态徽章/时长格式化等纯函数的吞吐

import { readFileSync, readdirSync, statSync } from "node:fs"
import { join } from "node:path"
import { performance } from "node:perf_hooks"

// ── 产物体积基准 ────────────────────────────────────────

interface AssetMetric {
    file: string
    kb: number
    gzipKb: number | null
}

function benchBundle(): void {
    const dist = join(import.meta.dir, "dist", "assets")
    const assets: AssetMetric[] = []
    for (const name of readdirSync(dist)) {
        if (!name.match(/\.(js|css)$/)) continue
        const size = statSync(join(dist, name)).size
        // gzip 体积：spawn 传 argv 数组（文件名不经 shell），失败留空
        let gzipKb: number | null = null
        try {
            const { spawnSync } = require("node:child_process") as typeof import("node:child_process")
            const gz = spawnSync("gzip", ["-c", join(dist, name)], { maxBuffer: 64 * 1024 * 1024 })
            if (gz.status === 0) gzipKb = gz.stdout.length / 1024
        } catch {
            gzipKb = null
        }
        assets.push({ file: name, kb: Math.round(size / 102.4) / 10, gzipKb })
    }
    assets.sort((a, b) => b.kb - a.kb)
    console.log("[bundle] dist/assets 体积")
    for (const a of assets) {
        console.log(
            `  ${a.file.padEnd(32)} ${String(a.kb).padStart(8)} KB` +
                (a.gzipKb != null ? ` (gzip ${a.gzipKb.toFixed(1)} KB)` : ""),
        )
    }
    const total = assets.reduce((s, a) => s + a.kb, 0)
    console.log(`  ${"TOTAL".padEnd(32)} ${String(total.toFixed(1)).padStart(8)} KB\n`)
}

// ── 纯函数吞吐基准 ──────────────────────────────────────

function median(values: number[]): number {
    const sorted = [...values].sort((a, b) => a - b)
    return sorted[Math.floor(sorted.length / 2)]
}

function bench(name: string, iterations: number, fn: () => unknown): void {
    // 预热
    for (let i = 0; i < Math.min(100, iterations); i++) fn()
    const durations: number[] = []
    for (let i = 0; i < iterations; i++) {
        const start = performance.now()
        fn()
        durations.push(performance.now() - start)
    }
    const med = median(durations)
    console.log(
        `  ${name.padEnd(36)} ${med.toFixed(4).padStart(9)} ms/op (${Math.round(1000 / med)}/s)`,
    )
}

function benchFormatters(): void {
    console.log("[formatters] 热路径纯函数（src/components/status.tsx）")
    // 与 src/components/status.tsx 保持一致的实现（该文件依赖 React，直接内联同逻辑）
    const formatDuration = (totalSeconds: number): string => {
        const s = Math.max(0, Math.floor(totalSeconds))
        const h = Math.floor(s / 3600)
        const m = Math.floor((s % 3600) / 60)
        const sec = s % 60
        const pad = (n: number) => String(n).padStart(2, "0")
        return `${pad(h)}:${pad(m)}:${pad(sec)}`
    }
    const STATE_CLASS: Record<string, string> = {
        recording: "bg-green-500",
        error: "bg-red-500",
        live: "bg-blue-500",
        offline: "bg-amber-500",
        stopped: "bg-gray-400",
        checking: "bg-purple-500",
    }
    const states = Object.keys(STATE_CLASS)

    bench("format_duration", 200000, () => formatDuration(3661.5))
    bench(
        "status_class_lookup x1000",
        200,
        () => {
            let acc = ""
            for (let i = 0; i < 1000; i++) acc = STATE_CLASS[states[i % states.length]]
            return acc
        },
    )
    // 模拟 646 任务列表的状态徽章文案解析（i18n resolveDictValue 路径）
    const zhDict = JSON.parse(readFileSync(join(import.meta.dir, "src/i18n/zh_CN.json"), "utf8"))
    const resolve = (path: string): string => {
        let cur: unknown = zhDict
        for (const part of path.split(".")) {
            cur = (cur as Record<string, unknown>)?.[part]
        }
        return typeof cur === "string" ? cur : path
    }
    bench(
        "i18n_resolve x646 (recordings list)",
        500,
        () => {
            let acc = ""
            for (let i = 0; i < 646; i++) acc = resolve(`quality.OD`)
            return acc
        },
    )
}

// ── main ────────────────────────────────────────────────

const selected = process.argv.slice(2).length ? process.argv.slice(2) : ["bundle", "formatters"]

if (selected.includes("bundle")) {
    try {
        benchBundle()
    } catch {
        console.log("[bundle] dist 未构建，先运行 bun run build\n")
    }
}
if (selected.includes("formatters")) {
    benchFormatters()
}
