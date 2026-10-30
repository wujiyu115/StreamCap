# AGENTS.md

## 功能验证要求（真实环境，禁止只跑类型检查就宣布完成）

任何功能性改动，报告完成前必须在用户实际接触的层面验证，并如实说明验证到哪一层、哪些没验证到。

### 验证层级（按改动所在层选择，逐层递进）

1. **模块级**：单元测试统一放 `tests/`（pytest），运行 `.venv/bin/python -m pytest tests/ -v`；监控调度/守卫/节奏门等纯逻辑一律先补单测（fake 网络层，见 `tests/helpers.py`），再用 `.venv/bin/python` 内联脚本打真实平台接口
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
- 后端接口拿不到真实数据时（如需要有效 cookie 的功能），可以先用 `page.route("**/api/**", handler)` 拦后端、`route.fulfill()` 喂合成数据，验证前端渲染/翻页/表单预填这类逻辑——这只能算前端验证，报告时要说明没验证到真实数据
- **要真 HTTP 验证「写配置」类改动、又不想碰仓库 `config/` 时，起一个隔离配置的服务**：`create_app(run_path)` 接受配置目录（CLI 不暴露，写个 5 行 launcher 调 `uvicorn.run(create_app("/tmp/xxx"))` 即可），SPA 仍从 `PROJECT_ROOT/frontend/dist` 伺服，所以 UI 也能直接打这个端口。真机验证「Cookie 覆盖前备份」「设置页到期提醒」就是这么做的（`/tmp/cookie_api_probe.py` + `/tmp/ui_cookie_expiry.py` 打临时端口，用完删目录）

### 项目特有陷阱

- **抖音关注列表**（`app/core/platforms/douyin_following.py` + 前端 `/following`）：必须是**含登录态**的 douyin cookie（`sessionid` 等）。只有 `ttwid` 的公共 cookie 会被判「未登录」（`status_code` 8 / 20003），接口一律返 `err.douyinLoginRequired`；没配 cookie 返 `err.douyinCookieMissing`。验证这个功能前先确认用户已在设置页贴了有效 cookie，否则只能验错误路径。翻页按抖音约定：第一页 `source_type=2&max_time=0` 握手拿 `max_time`，之后 `source_type=1` 翻页；请求还要带**自己的** `sec_user_id`/`user_id`（来自 `/aweme/v1/web/user/profile/self/`），只带 cookie 会返回空列表
- **关注列表翻页游标是上一页的 `min_time`，不是 `max_time`**（2026-10-21 用一个 429 关注的账号全量翻完验证）：响应里 `max_time`/`min_time` 是本页时间窗的上下界，下一页要传的 `max_time=上一页的 min_time`；原样回传上一页的 `max_time` 只会得到同一窗口整体后移一位的**重叠**数据（表现为「翻页后的人跟上一页几乎一样，只有第 1 个不同」）。另外 `min_time` 缺失/为 0 时必须置 `exhausted`，否则游标 0 会在 `/following?page=999` 这类手改参数下原地死循环。`FollowingStore.following_page` 单次请求最多翻 `FOLLOWING_WALK_MAX` 页（游标链只能顺序翻，不封顶会一个请求打几十上百次接口、把平台节奏门的风控预算吃干）
- **推荐直播（`live.douyin.com/webcast/feed/`）的三个坑**（`app/core/platforms/douyin_live_feed.py`，2026-10-26 真机验）：①`source_key` 必须是 `web_homepage_hot_web_live_card`，写 `drawer_hot_live` 之类会 `status_code:0` 但 `data` 空（静默失败，极易误判成「要登录」）；②**完全不带 Cookie 会被 444 空响应拒答**，匿名必须先拿一条 ttwid（`GET https://live.douyin.com/` 的 Set-Cookie，内存缓存 6h，失败换一条重试），但**不需要 a_bogus**；③**没有真分页**（`extra.max_time` 是服务器当前毫秒时间戳，回传只得重叠数据，一页固定 20 条不看 `count`），只能「换一批」。带登录 Cookie 时强个性化（同一账号连拉两批重叠 0/20、每批全新，与匿名热门榜完全不同的池），Cookie 失效不报错而是自动退回热门榜（`extra.user_extra_info` 落回 `revenue_user_label:-5 / life_layer_2:-1` 哨兵值）——所以「关注」页的推荐 tab 无需「Cookie 失效」分支，也因此**没登录也能用**
- **抖音「取关」别再说「封死」**：2026-10 曾因直连 `POST /aweme/v1/web/commit/follow/user/` 返回 **403 空 body** 而结论为「平台封死写接口」，2026-10-27 复查 GitHub 后推翻——真正缺的是 **`x-secsdk-web-signature`**（Argus 的拒绝理由就是 `403 Blocked by ArgusSecurityPlugin Uifid Not Found`），而它就是一句 `md5(f"{uifid}_{ts}_A96D855A08C0A9707F8BEF0D9A527E4E_{canonical_query}")`，纯 `hashlib` 即可，不需要浏览器/`cryptography`。服务端真取关需要拼齐：secsdk 签名 + `a_bogus`（含 body）+ 真 `msToken`（生产 cookie 里**没有**，需 mssdk 铸）+ `x-secsdk-csrf-token` + `bd-ticket-guard-client-data`（总 `/passport/ticket_guard/get_client_cert/` 实时拉服务端证书，纯 Python 只需 `ecdsa` 包）+ `x-tt-session-dtrait` + `curl_cffi` 的 TLS 指纹；成本半天到一天，属易腐资产。参考实现：`cv-cat/DouYin_Spider`（utils/secsdk_web_sign.py、bd_ticket.py、dtrait.py、mstoken.py）、`Evil0ctal/Douyin_TikTok_Download_API`（signing/native/websign.py）、`3441293738/creatorhub`（`set_follow_state()` 真实现了直连取关）、`ZhangYukwiver/DouyinDataReview`（directWrites.mjs）。注意 `type` 枚举两家不一致（0 vs 2 为取关），要实测。目前代码里仍是「跳转去抖音手动取关」入口（`docs/douyin-unfollow.md` 有完整时间线与验证方案）
- **关注列表给未开播的人加监控用短号拼地址**（2026-10-21 验 4/4）：`https://live.douyin.com/{short_id}`（无短号回落到 `unique_id`）实测能解析回同一个主播，所以点「添加监控」不必等对方开播。关注列表接口本身**不给** `room_id`，「在播」只能靠 `/webcast/web/feed/follow/` 的返回按 `sec_uid` join；该接口 `tag_name` 在 **item 层**（且常为空字符串），在播房间 `room.status=0`，与 `room/enter` 里 `4=未开播` 不是同一套枚举，别照搬判据
- **Tabs + 自定义 display 类**：`TabsContent` 只靠 Radix 的 `hidden` 属性隐藏非激活面板，而 `hidden` 会被任何 `display` 声明覆盖——内容区里写了 `flex`/`block` 就必须再带 `data-[state=inactive]:hidden`，否则隐藏面板仍占 flex 空间（表现为页面中部一大块空白）

- **B 站上传限流按 `pre_upload` 计，不按稿件计**（2026-10-28 生产取证）：`upload_jobs.message` 里存着 biliup 原始输出，48 条已完成任务逐条数——`pre_upload` 次数**恒等于文件数**（5P→5 次、1P→1 次），`Web 接口投稿成功` 恒为 1 次。源码上调：`crates/biliup-cli/src/uploader.rs:545` 在 `for` 循环里逐文件 `line.pre_upload()`，循环结束才 `submit_by_web()`；每个文件必须单独拿上传 token，无法批量。所以 406「您上传视频过快」一单多P 与一单一P **完全等价**（甚至更危险：5 次 pre_upload 挤在 1 分钟内），而 21564/21566「投稿过于频繁」才按稿件数算。据此 `uploads.min_interval_seconds`（默认 1800）是**每个文件/分P** 的间隔，`upload_channels.pacing_until = claim 时刻 + 文件数 × interval`，`uploads.max_pages_per_job`（默认 5，生产实测 5 次连发安全）给一单封顶、超出切多稿。事故边界实测：13 次 pre_upload / 13 分钟 → 406；406 后 15 分钟手动恢复即可成功（窗口不长）。
- **跨 event loop**：平台信号量（`platform_semaphores`）绑定在后台监控线程的 loop；FastAPI 端点里不能直接 await 这些 asyncio 原语（会报 `bound to a different event loop`）。需 `services.run_coro(coro)` 提交到后台 loop，再 `asyncio.wrap_future(fut)` 等结果（参考 `app/server/routers/recordings.py` 的 check-validity 端点）
- **上传任务的暂停/删除只能作用于非 `uploading` 的行**（`app/core/upload/manager.py` 的 `pause`/`delete`，路由 `/api/uploads/{id}/pause|resume|DELETE`）：正在跑 biliup 时杀进程会得到「不知道投没投成功」的半截稿件，所以后端一律 409 `err.uploadJobBusy`（前端此时连按钮都不渲染）。`paused` 必须同时进 `_ACTIVE_STATUSES`（否则同一文件重新入队会多排一条）和 `_prune_jobs` 的排除名单（否则超 `MAX_JOBS` 时手动暂停的任务会被清掉），单测 `test_pause_resume_and_delete_job` / `test_prune_jobs_keeps_paused_tasks` 守着。删除只删队列记录，不删磁盘视频。注意 `settings.uploadResume` 是**渠道级**「恢复上传」（限流冷却后用），行级按钮用的是 `settings.uploadJobResume`，别混
- **队列级暂停是 `upload_channels.status='paused'`，不是 `uploads.enabled`**：`uploads.enabled` 只挡「识别后自动入队」（`manager.py` 的 `enqueue_pose_outputs`），调度线程 `_run()` 压根不看它——想停投递只能停渠道。`POST /api/uploads/channels/{provider}/pause` 置 `paused`，`_next_job()` 的 `channel_status='active'` 判断天然生效；`paused` 与自动冷却 `cooling` 的区别是**不会自己到期**。从 `paused` 恢复时**不清节奏门**（`last_attempt_at`/`pacing_until` 保留），否则暂停前刚投过一单，一点「继续」就又撞 406；从 `cooling`/`blocked_auth` 恢复才清零
- **投递前必须查文件还在不在**（`manager._files_on_disk`，在 `_process` 里先于 `provider.upload` 跑）：手动删了视频不能让 biliup 报错烧光 5 次重试。三种分支——部分缺失→缩成剩下的分P 继续投（`files_json` 就地改小）、全缺但目录还在→直接 `failed`（`file(s) deleted from disk`，重试没意义）、全缺且目录也没了→当存储没挂上（NAS 掉线），放回 `retrying` 且 `next_attempt_at=now+900`，**两种都要把领任务时 +1 的 attempts 退回**。单测 `test_partially_deleted_files_upload_the_rest` / `test_all_files_deleted_fails_fast_without_burning_retries` / `test_unmounted_storage_keeps_job_retrying` 守着
- **别在 `async def` 端点里做阻塞 I/O**：整个 API 跑在单个事件循环上，一处阻塞 = 整个 WebUI 冻住到那个请求返回。踩过的坑：提交人体识别任务时 `os.walk` 走 NAS 共享 + 逐个文件 `fuser` 子进程 + 无句柄工具时每个文件 `time.sleep(2)` 采样 mtime，提交一个目录能把 WebUI 卡死几十秒（`filter_ready` 现在批量共享一轮采样，端点整段丢 `asyncio.to_thread`）。同类：`manager.stop()` 等子进程退出最多 13s、`media_service.stats` 递归统计录制目录。判断标准：文件系统递归、子进程、`sleep`、等进程退出，一律 `await asyncio.to_thread(...)`。`tests/test_pose_submit_nonblocking.py` 用「处理器执行期间事件循环还能推进多少次心跳」守着这条线

- **抖音风控**：实测阈值约 90 次/分钟/IP+cookie（2026-09 生产日志：每轮前 ~90 个请求全成功、之后全部返回风控页 → handler 内 `JSONDecodeError`）。对策：`_platform_slot` 平台节奏门（`monitor_platform_min_interval_seconds` 默认 2s，监控与有效性检测共享预算）+ 批量检测走后端缓存（`config/room_validity.json`，失效永久跳过、有效 6h TTL、error 必重检、URL 变更视为未检）+ 单次请求 limit 条数分批（前端按 pending 循环拉取、批间隔 2s）+ 每条随机抖动（0.3–0.8s）；cookie 未配置时 streamget 用内置公共 ttwid（写死在库里，共享且有寿命），量大易风控，可建议用户在设置里配置含 `ttwid=` 的完整 cookie
- **handler 异常被吞**：所有平台 handler 的 `get_stream_info` 被 `@trace_error_decorator` 包裹，异常时返回 `[]`，上层拿不到异常细节。需要区分错误原因（如"房间不存在"vs"网络失败"）时绕过 handler 直接调 streamget 原始接口（参考 `app/core/platforms/room_validity.py`，用 `process_data=False` 拿原始 JSON 看 `status_code`）
- **Cookie 登录态寿命只能从 `sid_guard` 读，而且真实串里它是 URL 编码的**：抖音网页登录态唯一带寿命的字段是 `sid_guard=<sessionid>|<issue_ts>|<ttl_seconds>|<http-date>`，但浏览器/插件复制出来的 cookie 串把 `|` 写成 `%7C`、日期里的空格写成 `+`（**生产那份就是**）——解析前必须 `urllib.parse.unquote`，只认裸 `|` 会把真 cookie 判成 `unknown`（2026-10 真机验证抓到的 bug，单测用裸 `|` 写所以没暴露）。实测寿命 60.0 天（`ttl_seconds=5184000`）；`bd_ticket_guard_*` 那两三个同名前缀字段不带寿命，别抓错
- **Cookie 覆盖前三重保护（`config/backups/`）**：`ConfigManager.save_cookies_config` 是唯一写 `config/cookies.json` 的出口（设置页 PUT / 扫码回填 / 初始化都走它），覆盖前先备份上一份、原子写、按文件名时间戳保留最近 5 份（名字带毫秒后缀，别退回秒级——同秒两次写入会撞名互相覆盖）。**只有「当前内容里至少有一个非空 Cookie」才备份**，否则一次误写空值会把好备份轮换掉，备份等于没做（有单测守着）。备份目录绝不能进仓库（`.gitignore` 有 `/config/backups/`），生产上确实丢过一次有效 Cookie，只能靠别处捞回来
- **自动上传分渠道（B 站 / 抖音），三层开关别混**：`uploads.enabled` 是总闸（关掉谁都不投）、`uploads.providers.<p>.enabled` 是渠道闸（关掉只不领该渠道新任务，队列原样保留、正在投的那单跑完）、运行态 `upload_channels.status='paused'` 是手动暂停（不自动到期）。节奏 `providers.<p>.min_interval_seconds` 是**稀疏覆盖**：不写=跟随全局，设置页「跟随全局（N 分钟）」按钮走 `resetKey` 删覆盖键，而不是把全局值写回渠道（写回就变成「显式设成和默认一样」，以后改全局不会跟随）。分P 上限也按渠道算：`_max_pages(provider)` 看 provider 的 `supports_multi_parts`，抖音为 False 时恒为 1（一视频一作品），B 站才用 `max_pages_per_job`
- **抖音投稿（`providers/douyin.py`）走创作者中心网页自动化，判定坑**：①`_looks_like_standard_mp4` 不能只看「前 1MB 有没有 moov 字样」——直录 mp4 用 `+empty_moov` 写的是**空壳 moov**，真轨道信息在 moof 分片里，会误判成标准 mp4 上传后才失败；正确做法是按顶层 atom 走一遍，见 `moof` 直接拒（`ffmpeg -i in.mp4 -c copy -movflags +faststart out.mp4` 可无损重封装）；②`detect_rate_limit` 把「滑块/短信验证」也算风控（否则纯滑块提示会一路走到超时，把失败算进 `attempts` 而不是冷却渠道），但调用前必须**先判 `detect_auth`**，否则登录框里的「验证码登录」会被误判成风控；③`_FAILURE_MARKERS` 长词在前（「视频上传失败」要先于「上传失败」命中）；④DOM 改版频繁，每处选择器都给 2–3 个候选；⑤默认必须投成「仅自己可见」并回读 `input.checked`，点「发布」在抖音侧就是公开
- **抖音扫码登录慢在哪（2026-10-27 拆解，74s → 29s）**：`PlaywrightDriver` 用真浏览器打开抖音、点「登录」再读二维码 `data:` URL。生产容器（4C / loadavg 2.5 / 广州电信出口）实测三段：`goto` 6s、**等「登录」入口渲染出来 ~25s**、**点开登录框到出码再 ~44s**。后两段都是抖音前端自己的开销：登录框要从 `yhgfb-cn-static.com`、`douyin-pc-web.douyinstatic.com` 现拉 secure-consumer-sdk / uc-secure-dtrait-core / rc-verifycenter/@latest 等一堆风控 JS，再打 `/passport/web/challenge/` 才给 `get_qrcode`。两个改法（都已落地）：① `launch_persistent_context` 用 `config/douyin_login_profile` 复用 HTTP 缓存（省 ~17s，只读挂载失败会自动退回无缓存模式）；② 入口页从首页换成 `https://www.douyin.com/user/self`（播放器 chunk 少，登录按钮早 6s 出现，打不开回落首页）。另外去掉了 goto 后的固定 3s sleep（改成立刻轮询点登录）。**profile 目录存着登录态 Cookie，和 cookies.json 一个等级**：`/config/douyin_login_profile/` 已进 `.gitignore`，且每次 `open()` 先 `clear_cookies()`——不清的话上次的会话会让页面不显示「登录」入口，扫码流程直接判「找不到登录入口」
- **`GET /api/douyin/cookie` 不走 `require_cookie`**：它必须在**没有登录态**（也就是最该提醒续期的时刻）也能返回 `state`，所以只解析本地 cookie 串、不打抖音接口；解析不出来一律 `unknown`（用户可能只填 ttwid / 手改过），只有真的空值才是 `missing`。前端日期用 `formatDate(ts, lang)`（`frontend/src/i18n/index.ts`）：默认的 `toLocaleDateString()` 跟浏览器语言走，中文界面会被渲染成 `12/20/2026`
- **MP4 直录是 fragmented 容器**：ffmpeg 录制用 `-movflags +frag_keyframe+empty_moov+faststart+delay_moov`（防崩溃设计，见 `app/core/media/ffmpeg_builders/video/mp4.py`），产物是 ftyp+moov(头部)+moof/mdat 分片结构。实测（lavfi 复现，同 muxer 参数）：手动停（SIGINT 优雅停）与 SIGKILL 强杀都保留头部 moov（empty_moov 即时写入），差别只在最后一个 fragment 是否收尾——手动停的文件结构完整、ffprobe/VLC 可读可播、人体识别可直接处理；强杀的最后一个 fragment 可能截断。**但时长元数据不可靠**（5s 内容 ffprobe 报 480s，2s 分段报 16.7s），专用播放器进度条/Seek 会怪。转标准 mp4：`ffmpeg -i in.mp4 -c copy -movflags +faststart out.mp4` 无损重封装（秒级）；识别原始分片式：顶层 atom 出现 moof。mp4 直录目前没有录制后 remux，remux 仅存在于 ts→mp4 转码路径
- **Playwright Chromium 无 H.264/AAC 解码器**：无系统 Chrome 时，headless Chromium 对**任何** h264 mp4（含正常封装）都报 MediaError code 4 / readyState=0——浏览器端"能否播放"验证完全失效，不可据此断定容器损坏。可播性结论必须真机（iOS Safari）或带专有解码器的 Chrome（channel="chrome"）。2026-09 之前"浏览器直接播放会卡死"的记录即受此污染，待真机复核
  - **但 MSE 路径例外（2026-09 实测）**：抖音直播 flv 经 mpegts.js 灌进 MSE 后，headless Chromium 里 `readyState=4`、`currentTime` 正常推进、`videoWidth/Height=1088x1920`、截图有画面——**flv/MSE 的"能不能播"可以在 headless 里真验**（`/tmp/ui_live_player.py` Phase A 就是靠这个断言"真出画面"）。raw mp4 直连 `<video src>` 仍然验不了
- **内嵌直播播放器**（`frontend/src/components/live-player-dialog.tsx`，关注页卡片上的「播放」）踩过的坑：
  - `canPlayType("application/vnd.apple.mpegurl")` 在**桌面 Chromium 回非空**（"maybe"），但把 m3u8 灌进 `<video>` 只会永远黑屏（没引 hls.js）。判"能否原生播 hls"必须 UA 门：iOS（含 iOS 版 Chrome/Firefox，都是 WebKit）/ 桌面 Safari / Android Chrome 才算；桌面 Chromium/Edge/Firefox 一律算不支持 → 给"H.265 房间去抖音看"兜底
  - **死链、CDN 卡住、编码不支持都不触发 `error` 事件**：mpegts.js 对 404 只打 console（`[IOController] Loader error`），video 可能永远 `readyState<3` 且无 error。必须有起播看门狗（15s 未 `readyState>=3` → 重取一次，再 15s → 失败兜底）。判"起来了"用 `readyState>=3`，**别用 `!paused`**（用户手动暂停会被误判成失败）
  - **自动重取预算不能随"地址变化"复位**：把计数器清零写在 `useEffect(..., [playUrl])` 里 = 新地址→预算清零→又失败→又重取，每 15s 无限循环。预算只按房间（`webRid`）复位，用户点「重试」是显式动作另行复位。回归：mock 每次重取都返回**不同**地址，断言请求数封顶
  - **同地址重试必须重建播放器**：装载 effect 只依赖 `playUrl` 时，重试拿到同一个地址 → effect 不重跑 → mpegts 实例停在失败态 → 界面永远"加载中"。用 react-query 的 `dataUpdatedAt`（每次取数成功都会变）同时进装载 effect 与看门狗依赖
  - **兜底面板的按钮会被底部工具栏盖住**：`.player-stagebar` 是 `z-10`、失败/未开播面板没设 z-index → 用户点不动「重试」（Playwright 报 `intercepts pointer events`）。失败/未开播这类阻塞态下要收起底部工具栏
  - **竖屏直播别用固定宽舞台**：`max-w-4xl` 舞台里放 9:16 直播 → 两边各留 ~250px 黑边。用 `loadedmetadata`/`resize` 量 `<video>` 的真实盒子宽度回写给舞台（移动端 `≤640px` 走铺满视口的沉浸布局，不参与这个联动）
  - 播放直链**不需要后端代理**：抖音 CDN 回 `access-control-allow-origin: *`，签名无 IP 字段（7 天有效），只需把直链 http 升级成 https 防反代部署的 mixed content

## Unraid 生产容器更新流程

StreamCap 的生产部署目标是 Unraid 7.x 服务器 **tank**（`192.168.31.139`），容器 `streamcap` 镜像为 `wujiyu115/streamcap:latest`，持久化目录 `/mnt/user/appdata/streamcap/config:/app/config`、下载盘 `/mnt/disk1/adult:/app/downloads`、端口 `6006`。代码推上 main 后 CI 自动构建镜像，但**容器不会自己刷新**，必须手动走一遍下面的流程才算完成。

### 前置条件

- SSH 免密已配：`ssh -i ~/.ssh/id_rsa_nas root@192.168.31.139` 直连，不要密码登录
- GitHub Actions 工作流：`.github/workflows/docker.yml`，`push` 到 main 自动构建并推 `:latest` + `:{sha}` 到 Docker Hub（用 `secrets.DOCKERHUB_USERNAME/TOKEN`）；多架构（`linux/amd64,linux/arm64`）+ QEMU，registry buildcache（`mode=max`）。**耗时看缓存**：层缓存命中 1–2 分钟；改了 Dockerfile / requirements（包在镜像里加了 playwright+chromium 后镜像 2.62GB）导致缓存失效时实测 ~17 分钟（2026-10）。判断「卡住」前先看 run 的 step 明细（`/actions/runs/<id>/jobs` 的 `Build and push` 步骤是否已 completed），别只看 run 的 `status=in_progress`

### 流程（5 步）

1. **本地提交推送**
   ```
   git commit -m "..." && git push origin main
   ```
2. **确认 CI 跑过**
   - 有 `gh`：`gh run list --repo wujiyu115/StreamCap --limit 3` 看最近一次 `completed success`
   - 没 `gh`（未登录）：走未认证 GitHub API `curl https://api.github.com/repos/wujiyu115/StreamCap/actions/runs?per_page=3`，按 `head_sha` 对应本次 commit，看 `status=completed / conclusion=success`
   - **不要只凭本地 `git push` 成功就往下走**——CI 可能编译失败
3. **Unraid 上拉新镜像**
   ```
   ssh -i ~/.ssh/id_rsa_nas root@192.168.31.139 \
     "docker pull wujiyu115/streamcap:latest"
   ```
   输出里看到 `Downloaded newer image` 或新 digest 才算拉到新版本；`Image is up to date` 表示本地就是最新（可能 CI 还没完成、或 tag 没变）
4. **用 Unraid 官方脚本重建容器**（保留 template 里的所有绑定 / 端口 / 环境变量）
   ```
   ssh -i ~/.ssh/id_rsa_nas root@192.168.31.139 \
     "/usr/local/emhttp/plugins/dynamix.docker.manager/scripts/rebuild_container streamcap"
   ```
   这个脚本读 `/boot/config/plugins/dockerMan/templates-user/my-streamcap.xml`，`docker run` 起新镜像、删旧容器、清旧镜像；**严禁**直接 `docker stop + docker run` 拼参数，否则 UI 上的配置变更会丢
5. **启动 + 自检**
   - `streamcap` **不在 autostart 列表**（`/var/lib/docker/unraid-autostart`），`rebuild_container` 会按 Unraid 惯例重建后自动 `stop`，所以第一次跑完 `docker ps -a` 看到 `Exited (137)` 是正常的
   - 启动：`docker start streamcap`，等 30–60s 健康检查
   - 自检：`docker ps --filter name=streamcap` 看 `Up ... (healthy)`；`curl http://localhost:6006/api/recordings` 看任务数（生产 ~170+）与 `is_recording` 字段正常；`docker logs --tail 30 streamcap` 看监控轮询在跑

### 关键禁忌

- **不要在有任务 `is_recording=True` 时重建**：rebuild 会 `docker stop`（SIGTERM → 升级成 SIGKILL），正在录的 ffmpeg 产物最后一个 fragment 可能截断。先通过 API `GET /api/recordings` 确认所有 `is_recording=false`
- **不要绕过 template 用裸 `docker run`**：Unraid 的 Web UI 把用户配置持久化在 XML 里，裸 docker run 出来的容器在 UI 上显示"orphan"，下一次点 Update 会丢配置
- **不要凭"已拉新镜像"就宣布完成**：对比 `docker inspect streamcap --format '{{.Image}}'` 与 `docker images wujiyu115/streamcap:latest --format '{{.ID}}'` 的 image ID 是否一致，确认新镜像已经跑起来
- **重建后 `docker ps` 看不到 streamcap 别慌**：先看 `docker ps -a`，如果 `Exited (137)` 就是 autostart 行为，`docker start` 即可；只有 `Created`（从未跑过）才需要排查 template 或镜像问题

### 配置分层（默认层在镜像里，用户层只存改过的键）

配置有两层，**生效值只在内存里合并，永不落盘**：

| 层 | 位置 | 谁写 | 内容 |
| --- | --- | --- | --- |
| 默认层 | 镜像内 `/app/config_templates/default_settings.json`（开发时 `config/default_settings.json`） | git | 全量键 |
| 用户层 | 挂载卷 `config/user_settings.json` | UI / 手改 | **只有被显式改过的键**（稀疏） |

- `SettingsConfig.user_overrides` 是稀疏用户层（持久化的那份），`SettingsConfig.user_config` 是 `deep_merge(default, overrides)` 的生效字典。业务代码继续读 `user_config`（`StreamManager` 等在构造时按引用捕获它，所以刷新用 `rebuild_effective` 原地 `clear()/update()`，别重新赋值）。
- 只有 `user_overrides` 会被 `save_user_config` 写盘。`app/core/config/layering.py` 里全是纯函数（`deep_merge` / `rebuild_effective` / `apply_patch` / `unset_path` / `strip_defaults` / `config_hash`），改配置语义先看那儿。
- `PUT /api/settings` 收 `{patch, version}`：patch 是**前端 diff 出来的增量**（只含用户这次真正改动的键），`version` 是 `config_hash(user_overrides)`，不匹配返回 409 `err.settingsVersionMismatch`；没带 `patch` 的老前端一律 409 `err.settingsStaleClient`（宁可让旧标签页报错，也不接受整体写回）。
- `DELETE /api/settings/keys/{path}` 从用户层删键（回落到默认值），设置页里"已覆盖"徽章后面的按钮就是它。
- 稀疏性来自**写入路径**（前端只发 diff），不是保存时"把等于默认值的键剔掉"——后者会把"显式设成和默认值相同"和"继承默认值"混为一谈，以后改默认值时前者会被静默跟着改。唯一的例外是一次性迁移。

**所以新增设置项时**：往 `config/default_settings.json` 加键随便加，加错默认值也不会顶掉生产上已有的用户值（那些键在用户层里，合并时优先）。要注意的只有反过来的方向——**改一个用户从没在 UI 上动过的键的默认值，等于给所有部署改行为**，这是有意为之才行。

`app/core/pose/pose_params.py` 的 `DEFAULTS` 是 `default_settings.json` 里 `pose_detection` 的镜像副本，改一处要同步另一处。

#### 一次性迁移（老部署的 user_settings.json 是全量的）

`ConfigManager._migrate_sparse_user_settings` 在启动时跑一次：把等于默认值的键剔掉（这次是有意的有损操作），备份到 `user_settings.json.pre-sparse.bak`，被剔掉的键打日志，然后在 `config/.config_migrations.json` 里记 `sparse_user_settings: true`，不会重复跑。挂载卷里那份历史遗留的 `default_settings.json`（老代码复制过去的，对老键永久停留在首次部署时的值）会被重命名成 `default_settings.json.legacy`，之后默认层只从镜像读。

排查配置问题时看 `GET /api/settings` 的三个字段：`default_settings`（镜像默认层）、`user_overrides`（稀疏用户层）、`user_settings`（合并后的生效值）。

### 回滚

- template 每次更新前备份到 `/tmp/my-streamcap.xml.bak`
- 回滚：`cp /tmp/my-streamcap.xml.bak /boot/config/plugins/dockerMan/templates-user/my-streamcap.xml` + 再跑一遍 `rebuild_container streamcap`
- 指定旧版本：改 template 里 `<Repository>` 为 `wujiyu115/streamcap:<旧sha或旧tag>` 再重建；Docker Hub 上每次 CI 同时推 `:{github.sha}`，可以精确回退到任意一次提交

### 扩展到其他容器

同流程改容器名即可：`rebuild_container <name>`。其他 `wujiyu115/*` 系列（`docknav`、`gamesearch`、`jxpan` 等）都按这套走；第三方镜像（`vaultwarden`、`homeassistant` 等）如果 template 里的 `<Repository>` 是 `:latest`，也能直接 `rebuild_container` 拉新版
