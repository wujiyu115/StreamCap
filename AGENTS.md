# AGENTS.md

## 功能验证要求（真实环境，禁止只跑类型检查就宣布完成）

任何功能性改动，报告完成前必须在用户实际接触的层面验证，并如实说明验证到哪一层、哪些没验证到。

### 验证层级（按改动所在层选择，逐层递进）

1. **模块级**：用 `.venv/bin/python` 内联脚本直接调用改动模块，用真实数据断言（不 mock、打真实平台接口）
2. **API 级**：`.venv/bin/python main.py --port 6006` 起服务，curl 走真实 HTTP；错误路径也要测（空参、不存在的 id、not_found）
3. **UI 级（前端改动必做）**：Playwright headless Chromium 走真实交互（点击、Dialog、删除联动）。`npm run build`/tsc 只是编译检查，**不算功能验证**

### 已知可用的真实测试数据

- 抖音有效房间：`https://live.douyin.com/M41736236688`（房间存在，长期未开播，主播"小葱头🍑"）
- 抖音失效判定：房间号改一位（如 `M41736236687`）或随机字符串 → 接口返回 `status_code` 10011/4001038，提示语是误导性的"当前服务繁忙"
- `config/recordings.json` 里有 10 条假 URL 测试任务（`live.douyin.com/test1`~`test10`），可作 invalid 用例；注意 `test9` 恰好是真实房间（主播"67."，剑网3）

### 测试数据保护

- 动 `config/recordings.json` 前先备份（cp 到 /tmp），测完恢复
- 通过 API 创建的测试任务，验证完必须删除，不留残留
- 验证脚本和截图放 /tmp，不进仓库

### Playwright 用法

- 环境：项目 venv 里 `pip install playwright`；chromium 已缓存在 `~/.cache/ms-playwright`，headless 直接可用
- 后端会伺服 `frontend/dist`（`_mount_spa`），UI 测试直接打后端端口即可，无需另起 vite dev server
- 原生 `confirm()` 用 `page.on("dialog", lambda d: d.accept())` 处理
- 涉及网络检测的断言给足超时，如 `expect(...).to_be_visible(timeout=120000)`

### 项目特有陷阱

- **跨 event loop**：平台信号量（`platform_semaphores`）绑定在后台监控线程的 loop；FastAPI 端点里不能直接 await 这些 asyncio 原语（会报 `bound to a different event loop`）。需 `services.run_coro(coro)` 提交到后台 loop，再 `asyncio.wrap_future(fut)` 等结果（参考 `app/server/routers/recordings.py` 的 check-validity 端点）
- **抖音风控**：批量检测走后端缓存（`config/room_validity.json`，失效永久跳过、有效 6h TTL、error 必重检、URL 变更视为未检）+ 单次请求 limit 条数分批（前端按 pending 循环拉取、批间隔 2s）+ 每条随机抖动（0.3–0.8s）；cookie 未配置时 streamget 用内置公共 ttwid（写死在库里，共享且有寿命），量大易风控，可建议用户在设置里配置含 `ttwid=` 的完整 cookie
- **handler 异常被吞**：所有平台 handler 的 `get_stream_info` 被 `@trace_error_decorator` 包裹，异常时返回 `[]`，上层拿不到异常细节。需要区分错误原因（如"房间不存在"vs"网络失败"）时绕过 handler 直接调 streamget 原始接口（参考 `app/core/platforms/room_validity.py`，用 `process_data=False` 拿原始 JSON 看 `status_code`）
- **MP4 直录是 fragmented 容器**：ffmpeg 录制用 `-movflags +frag_keyframe+empty_moov`（防崩溃设计），原始文件是 moov+moof/mdat 分片结构——ffprobe 能读但浏览器 `<video>` 直接播放会卡死（readyState=0 线性扫描）。容器强杀（docker rm -f）打断录制会留下此类文件。修复：`ffmpeg -i in.mp4 -c copy -movflags +faststart out.mp4` 无损重封装（秒级）；识别方法：扫描文件头前几个顶层 atom 出现 moof 即为原始分片式
