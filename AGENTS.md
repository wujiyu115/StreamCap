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

### 项目特有陷阱

- **跨 event loop**：平台信号量（`platform_semaphores`）绑定在后台监控线程的 loop；FastAPI 端点里不能直接 await 这些 asyncio 原语（会报 `bound to a different event loop`）。需 `services.run_coro(coro)` 提交到后台 loop，再 `asyncio.wrap_future(fut)` 等结果（参考 `app/server/routers/recordings.py` 的 check-validity 端点）
- **别在 `async def` 端点里做阻塞 I/O**：整个 API 跑在单个事件循环上，一处阻塞 = 整个 WebUI 冻住到那个请求返回。踩过的坑：提交人体识别任务时 `os.walk` 走 NAS 共享 + 逐个文件 `fuser` 子进程 + 无句柄工具时每个文件 `time.sleep(2)` 采样 mtime，提交一个目录能把 WebUI 卡死几十秒（`filter_ready` 现在批量共享一轮采样，端点整段丢 `asyncio.to_thread`）。同类：`manager.stop()` 等子进程退出最多 13s、`media_service.stats` 递归统计录制目录。判断标准：文件系统递归、子进程、`sleep`、等进程退出，一律 `await asyncio.to_thread(...)`。`tests/test_pose_submit_nonblocking.py` 用「处理器执行期间事件循环还能推进多少次心跳」守着这条线

- **抖音风控**：实测阈值约 90 次/分钟/IP+cookie（2026-09 生产日志：每轮前 ~90 个请求全成功、之后全部返回风控页 → handler 内 `JSONDecodeError`）。对策：`_platform_slot` 平台节奏门（`monitor_platform_min_interval_seconds` 默认 2s，监控与有效性检测共享预算）+ 批量检测走后端缓存（`config/room_validity.json`，失效永久跳过、有效 6h TTL、error 必重检、URL 变更视为未检）+ 单次请求 limit 条数分批（前端按 pending 循环拉取、批间隔 2s）+ 每条随机抖动（0.3–0.8s）；cookie 未配置时 streamget 用内置公共 ttwid（写死在库里，共享且有寿命），量大易风控，可建议用户在设置里配置含 `ttwid=` 的完整 cookie
- **handler 异常被吞**：所有平台 handler 的 `get_stream_info` 被 `@trace_error_decorator` 包裹，异常时返回 `[]`，上层拿不到异常细节。需要区分错误原因（如"房间不存在"vs"网络失败"）时绕过 handler 直接调 streamget 原始接口（参考 `app/core/platforms/room_validity.py`，用 `process_data=False` 拿原始 JSON 看 `status_code`）
- **MP4 直录是 fragmented 容器**：ffmpeg 录制用 `-movflags +frag_keyframe+empty_moov+faststart+delay_moov`（防崩溃设计，见 `app/core/media/ffmpeg_builders/video/mp4.py`），产物是 ftyp+moov(头部)+moof/mdat 分片结构。实测（lavfi 复现，同 muxer 参数）：手动停（SIGINT 优雅停）与 SIGKILL 强杀都保留头部 moov（empty_moov 即时写入），差别只在最后一个 fragment 是否收尾——手动停的文件结构完整、ffprobe/VLC 可读可播、人体识别可直接处理；强杀的最后一个 fragment 可能截断。**但时长元数据不可靠**（5s 内容 ffprobe 报 480s，2s 分段报 16.7s），专用播放器进度条/Seek 会怪。转标准 mp4：`ffmpeg -i in.mp4 -c copy -movflags +faststart out.mp4` 无损重封装（秒级）；识别原始分片式：顶层 atom 出现 moof。mp4 直录目前没有录制后 remux，remux 仅存在于 ts→mp4 转码路径
- **Playwright Chromium 无 H.264/AAC 解码器**：无系统 Chrome 时，headless Chromium 对**任何** h264 mp4（含正常封装）都报 MediaError code 4 / readyState=0——浏览器端"能否播放"验证完全失效，不可据此断定容器损坏。可播性结论必须真机（iOS Safari）或带专有解码器的 Chrome（channel="chrome"）。2026-09 之前"浏览器直接播放会卡死"的记录即受此污染，待真机复核

## Unraid 生产容器更新流程

StreamCap 的生产部署目标是 Unraid 7.x 服务器 **tank**（`192.168.31.139`），容器 `streamcap` 镜像为 `wujiyu115/streamcap:latest`，持久化目录 `/mnt/user/appdata/streamcap/config:/app/config`、下载盘 `/mnt/disk1/adult:/app/downloads`、端口 `6006`。代码推上 main 后 CI 自动构建镜像，但**容器不会自己刷新**，必须手动走一遍下面的流程才算完成。

### 前置条件

- SSH 免密已配：`ssh -i ~/.ssh/id_rsa_nas root@192.168.31.139` 直连，不要密码登录
- GitHub Actions 工作流：`.github/workflows/docker.yml`，`push` 到 main 自动构建并推 `:latest` + `:{sha}` 到 Docker Hub（用 `secrets.DOCKERHUB_USERNAME/TOKEN`）；多架构 + QEMU，通常 1–2 分钟跑完

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
