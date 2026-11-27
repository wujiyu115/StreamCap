# 抖音「取消关注」调研与结论

> 状态：**当前上线的是「去抖音取消关注」跳转入口**，不是真取关。
> 本文记录 2026-10-26 的失败结论、2026-10-27 对 GitHub 的复查（**推翻了当时的结论**），以及如果要真做还缺什么。
> 相关代码：`frontend/src/pages/following.tsx`（`askUnfollow`）、commit `00f2f80`。

## 1. 需求

StreamCap 的「直播」页（原关注页）列出自己抖音号关注的人，希望在卡片上直接「取消关注」。

## 2. 我们实测的时间线（2026-10-26）

目标接口：`POST https://www.douyin.com/aweme/v1/web/commit/follow/user/`，表单 `user_id` / `sec_user_id` / `type`（关注/取关）。

| # | 尝试 | 结果 |
| --- | --- | --- |
| 1 | 纯 HTTP POST，带生产 Cookie + `a_bogus`（和关注列表读接口同一套签名方式） | **HTTP 403，空 body** |
| 2 | 按 `ZhangYukwiver/DouyinDataReview` 注释换宿主 `www-hj.douyin.com` | 仍被拒 |
| 3 | Playwright 注入生产 Cookie 打开对方主页，看是否显示「已关注」 | 显示「关注」（被判未登录）；页内 `fetch('/aweme/v1/web/user/profile/self/')` → `status_code: 8` |
| 4 | 换成真实 Chrome UA / 指纹后再试 | 仍然未登录 —— 会话绑原浏览器指纹 |

**当时（错误的）结论**：抖音 2026-08 起给行为类接口加了站点侧签名，服务端无法复刻，因此「写接口被封死」，只能做跳转。

副产品教训：第 3 步把**生产 Cookie 注入另一个浏览器**，导致该登录态被平台作废，用户被迫重新扫码 —— 之后定下规矩：*生产的登录态不能拿去探写操作、不能注入别的浏览器*。

## 3. 复查 GitHub：上面的结论不成立（2026-10-27）

事实核对下来，403 的原因不是「签名不可复刻」，而是**我们漏了一个请求参数**。

### 3.1 关键的抖音签名是纯 md5，盐是常量

`x-secsdk-web-signature` 的算法（多份独立逆向结果一致）：

```
sig = md5(f"{uifid}_{timestamp}_{SALT}_{canonical_query}").hexdigest()
SALT = "A96D855A08C0A9707F8BEF0D9A527E4E"   # douyin_web 项目，写死在 VM 常量池
```

- 无 nonce、无会话状态，同一秒内两次调用签名完全一致。
- `canonical_query` = 除签名字段外的全部 query，保持原顺序，value 解码后用 `encodeURIComponent` 重编码，末尾追加 `&timestamp={ts}`；query 里没有 `uifid` 时 SDK 从 `UIFID` cookie 取值补上再签。
- 缺这个参数时，Argus 的拒绝理由明文写着：`403 Blocked by ArgusSecurityPlugin Uifid Not Found` / `... Signature Not Found`。
- 反编译路径（`cv-cat/DouYin_Spider` 记录）：CDP hook `XMLHttpRequest.open` 定位到 `runtime_bundler_34.js` 的 `webSignUrl`（VMP 字节码读不动），改 hook `CryptoJS.MD5` 直接读到被哈希的明文。

**只需要 `hashlib`，不需要 `cryptography`，也不需要浏览器。** 我们当时判断「必须浏览器侧凭据」是错的。

### 3.2 可参考的开源实现

| 仓库 | 语言 | 与本需求的关系 | 要点 |
| --- | --- | --- | --- |
| [`cv-cat/DouYin_Spider`](https://github.com/cv-cat/DouYin_Spider)（3.5k★，2026-10 仍更新） | Python | **最完整的服务端复刻套件** | `utils/secsdk_web_sign.py`（纯算 secsdk 签名 + 规范化规则逐字符实测）、`utils/bd_ticket.py`（ECDH+HKDF+HMAC 生成 `bd-ticket-guard-client-data`，服务端证书实时走 `/passport/ticket_guard/get_client_cert/`）、`utils/dtrait.py`、`utils/mstoken.py`（真实 msToken 铸造）、`utils/ab_pure.py` / `xbogus_pure.py`、`utils/sm3.py` |
| [`Evil0ctal/Douyin_TikTok_Download_API`](https://github.com/Evil0ctal/Douyin_TikTok_Download_API) | Python | secsdk 签名最干净的参考实现 | `src/dtk/signing/native/websign.py`，92 行含注释即完整；文档里点名了 `403 ... Uifid Not Found` |
| [`JoeanAmier/TikTokDownloader`](https://github.com/JoeanAmier/TikTokDownloader) | Python | 上一份的移植（Apache-2.0） | `src/encrypt/websign.py`，同样 92 行 |
| [`3441293738/creatorhub`](https://github.com/3441293738/creatorhub)（2.5k★） | Python | **真的实现了直连取关** | `app/platforms/douyin/client.py` 的 `set_follow_state()` → `/aweme/v1/web/commit/follow/user/`；带 msToken（cookie 没有就调 mssdk 铸）、`a_bogus`、`uifid`、`verifyFp`，靠 `curl_cffi` 的 `impersonate` 兜 TLS 指纹 |
| [`ZhangYukwiver/DouyinDataReview`](https://github.com/ZhangYukwiver/DouyinDataReview) | Node/JS | 有真取关，但需浏览器登录时抓凭据 | `collector/directWrites.mjs`：`type=0` 取关；请求带 `bd-ticket-guard-*`（ECDH/HKDF/HMAC）、`x-tt-session-dtrait`（AES-128-CBC+RSA）、`x-secsdk-csrf-token`；宿主换 `www-hj.douyin.com` |
| [`zhinjs/douyin-im`](https://github.com/zhinjs/douyin-im) | TS + Node native | 把 ticket-guard 做成了原生模块（`bdticket.node`），配套离线 oracle 校验；**但 README 自述 follow 尚未完成真实账号验收** | 说明 ticket-guard 是可离线复刻的，只是工程量大 |
| `virgilclyne/GetSomeFries`、`jackwener/OpenCLI`、`Sjj1024/douyin-live` 等 | JS/其它 | 只提到接口路径，无可用实现 | 参考价值低 |

### 3.3 修正后的结论

**服务端真取关是可行的**，不是「平台封死」。403 的直接原因是缺 `x-secsdk-web-signature`（可能叠加 TLS 指纹不匹配），而我们当时的探测因为「读接口不需要它」而完全没往这个方向想。

## 3.4 实测：签名链已通（2026-10-27 15:01）

用**不存在的 `user_id`**（`999999999999999999`，不产生任何真实关注关系）打 `POST /aweme/v1/web/commit/follow/user/`，得到下面这张矩阵（httpx，无 `curl_cffi`，无 msToken，无 ticket-guard）：

| 变体 | 结果 |
| --- | --- |
| `www` + 无 secsdk 签名（复现历史行为） | **403，空 body** |
| `www` + secsdk 签名（放 query） | 403，空 body |
| `www` + secsdk 签名 + 三个头 | 403，空 body |
| `www-hj` + secsdk 签名（只放 query） | 403（`x-alicdn-da-ups-status: endOs,0,403`，边缘拦截） |
| **`www-hj` + secsdk 签名 + 三个头** | **200** `{"status_code":8,"status_msg":"用户未登录"}` |

「三个头」= `uifid`、`x-secsdk-web-signature`、`x-secsdk-web-expire`（+ `x-secsdk-csrf-token` 取 cookie 里的 `passport_csrf_token`）。

**结论：403 墙已经过去了。** 返回的是**业务错误码**而不是风控拒绝，说明签名/TLS/参数这一层被服务端接受了，剩下的唯一变量是登录态（当时那份 Cookie 的 `www` 会话已失效，见下）。没有用 ticket-guard、没有用 dtrait、没有用 msToken、没有 `curl_cffi`。

签名拼装顺序（实测可用，与 `DouyinDataReview` 的 `signSecsdkUrl` 一致）：

```
query1 = 设备参数 + uifid + verifyFp/fp
query2 = query1 + "&a_bogus=" + quote(ab_sign(query1, UA))      # a_bogus 先签
query3 = 规范化(query2) + "&timestamp=" + ts                    # value 解码后重编码，末尾追加 ts
sig    = md5(f"{uifid}_{ts}_{SALT}_{query3}")
URL    = host + path + "?" + query3 + "&x-secsdk-web-signature=" + sig
```

顺带查出一个**会骗人的缓存**：`/api/douyin/following/self` 默认读 `FollowingStore` 的 10 分钟缓存，会话死了它照样返回上次的账号信息（我一开始就被它骗了，以为生产登录态还活着）。带 `?refresh=true` 立刻暴露：`HTTP 400 err.douyinLoginRequired`。判断登录态一律加 `refresh=true`。

## 3.5 第二轮：登录态有效时，写请求仍 403 且**会把会话弄死**（2026-10-27 15:1x）

用户重新扫码登录后（`self?refresh=true` → 200 / `status_code: 0`，账号 Helix），同一条请求再打：

| 步骤 | 结果 |
| --- | --- |
| `p.py` 先读 self | 200，`status_code: 0`（登录态确实活了）| 
| `p.py` 写接口 × 2（www / www-hj，secsdk 签名 + 三头已齐）| **403 空 body** |
| `p2.py` 再读 self | 200，`status_code: 8 用户未登录` ← **会话死了** |
| `p2.py` 写接口 × 6（现取 csrf 头 / 目标页 referer / 拼 msToken 等组合）| 全部 200 + `status_code: 8`（已经只是未登录，不是风控）|
| 回到生产 `self?refresh=true` | **400 `err.douyinLoginRequired`** |

文件 `cookies.json` 的 mtime / md5 全程未变（15:08 / `9baf7243…`），所以不是 Cookie 被改写或被别人刷掉——**是写请求本身把会话打死**。

推论：

1. `403` 是请求层拒绝，且**发生在会话还有效的时候**；不是「未登录」的别名。
2. 被拒之后会话随即失效，说明 Argus 把这次请求判为「异常设备/行为」并连带作废了登录态——这与 2026-10-26 那次「注入 Cookie 的浏览器被判未登录」是同一个机制：**会话绑浏览器侧凭据**。
3. 所以剩下缺的不是参数，而是那套**设备凭据**：`bd-ticket-guard-client-data`（P-256 ECDH + HKDF + HMAC，ticket 来自登录态）、`x-tt-session-dtrait`，以及可能的 `msToken`（生产 Cookie 里**没有**这个键）。这也解释了为什么 `DouyinDataReview` 的 `ree_path` 白名单里恰好包含 `/aweme/v1/web/commit/follow/user/`。

⚠️ **代价**：每失败一次探测就把会话打死一次，用户得重新扫码；对真实账号反复触发 Argus 也有被风控/要求验证的风险。要接着试必须用一次性小号，不能拿主号刷。

## 4. 真要打通还缺什么

按依赖顺序，逐项都是已开源可抄的：

| 件 | 现状 | 难度 |
| --- | --- | --- |
| `x-secsdk-web-signature` | 未实现 | **极低**：`md5(uifid_ts_SALT_canonical_query)`，~30 行，仅 `hashlib` |
| `a_bogus`（含 POST body digest） | `streamget` 的 `ab_sign` 已用于读接口 | 低（写请求要确认 body 是否进签名） |
| `uifid` / `verifyFp` / `msToken` | 生产 Cookie **里没有 msToken** | 中：需调 mssdk report 铸一条真 msToken（`cv-cat/utils/mstoken.py`、creatorhub `_prepare_ms_token`） |
| `x-secsdk-csrf-token` | 未实现 | 低：`/passport/web/strategy/`，带 `x-secsdk-csrf-request: 1` |
| `bd-ticket-guard-client-data` | 未实现 | 中高：ticket 来自**登录态**，服务端证书要实时拉；纯 Python 只需 `ecdsa`（纯 Python 包，不必 `cryptography`） |
| `x-tt-session-dtrait` | 未实现 | 中：AES-128-CBC + RSA PKCS#1，需 `cryptography` 或 `pycryptodome`；`dtrait` blob 与 sdkVersion 需从页面捞 |
| TLS / HTTP2 指纹 | 现用 `httpx` | 低：`curl_cffi` + `impersonate="chrome"`（creatorhub 就是这么过的） |

> 2026-10-27 实测修正：前面三项（secsdk 签名、csrf、a_bogus）已由我们亲手验证过——签名链能过。卡住的是**最后三项里的设备凭据**（ticket-guard / dtrait / msToken）。

**枚举分歧要实测确认**：取关的 `type`，`DouyinDataReview` 用 `0`（关注用 `1`），`creatorhub` 用 `2`（关注用 `1`）。两者不可能都对，写错就是业务错误码而不是 403。

## 5. 建议的验证顺序（关键：不牺牲生产登录态 / 不拿主号刷）

1. **先验签名链，不动任何真实关系**：拿一个**不存在的 `user_id`**（如 `1` 或随机 19 位）打 `commit/follow/user/`。
   - 返回业务错误码（`status_code != 0`）⇒ 请求层被接受，签名/TLS 都对，继续；
   - 返回 403 / 空 body ⇒ 还缺东西（优先怀疑 ticket-guard / dtrait）。
   - 这一步不产生任何关注或取关，风险最低。**注意：2026-10-27 实测证明连这一步都会把会话打死**（见 §3.5）——所以这一轮和后面所有轮都应该在**一次性小号**上做，不要用主号。
2. 用**一次性小号**的登录态做真实验证：关注一个目标 → 取关 → 读回 `follow_status` 确认。
3. 两项都过，再谈接进 StreamCap（后端 `douyin_following.py` 旁边加 `douyin_write.py`，前端把跳转换回真按钮 + 二次确认 + 失败回滚提示）。

## 6. 成本与风险（如果要立项）

- 代码量：签名 30 行 + msToken 铸造 ~100 行 + csrf/ticket-guard/dtrait ~250 行 + 写接口封装与状态核验 ~80 行 ≈ **半天到一天**，外加新依赖 `ecdsa`、`curl_cffi`（`cryptography` 视 dtrait 是否必需）。
- 风险：抖音这条链路在 2026 年改过两次（8 月加站点签名、10 月起写请求走 `www-hj`），复刻代码属于**易腐资产**；一旦失效，表现是「点了没反应」而不是明确报错，需要读回 `follow_status` 做二次确认。
- 频控：取关是行为接口，节奏门（`_platform_slot`）必须共用，且不可批量连点。

## 7. 当前代码状态（`00f2f80`）

- 关注卡片上的按钮是 `<a href="https://www.douyin.com/user/{sec_uid}">`，点击先弹确认框说明「抖音已不开放第三方直接取消关注」，确认后新标签页打开对方主页，用户自己点「已关注 → 取消关注」。
- i18n：`following.unfollow`（去抖音取消关注）、`following.unfollowHint`（带主播名）。
- 仓库里**没有**残留的半成品写接口代码（`commit_follow()`、`POST /api/douyin/following/unfollow`、6 个单测都已在当时移除）。

## 8. 2026-11-24 实测：创作者后台（creator.douyin.com）这条路通了

推翻 §4 那套「要拼 ticket-guard / dtrait / msToken / curl_cffi」的成本估算——**换个后端域名就够了**。下面是生产账号上真跑出来的结果（每次写操作都有回读校验）。

### 8.1 接口（从官方前端 bundle `pages-following.*.js` 扒的原文）

```js
GET  /aweme/v1/creator/relation/following/list/         // ?cursor=<游标>&count=20
POST /aweme/v1/creator/relation/following/cancel/       // body {"user_id": "@..."}   取关
POST /aweme/v1/creator/relation/following/create/       // body {"user_id": "@..."}   关注
POST /aweme/v1/creator/relation/following/create/v2/    // 申请关注
POST /aweme/v1/creator/relation/following/multi/cancel/ // 批量取关（body 键名未知，见 8.5）
GET  /aweme/v1/creator/relation/follower/list/          // 粉丝
```

宿主是 `https://creator.douyin.com`。`user_id` 不是 sec_uid，是列表里 `user_info_list[].user_id` 那种加密句柄（形如 `@j/dp7bJMT0//sshS8...`），**必须先拉列表拿句柄**。

### 8.2 三条关键实测结论

1. **登录态直接复用**：生产扫码登录用的持久 profile（`config/douyin_login_profile`）打开 `creator.douyin.com/creator-micro/data/following/following` 就是已登录状态，不需要单独登录创作者中心。
2. **读接口完全不验签**：把模板 URL 里的 `a_bogus` 改垃圾 / 删 `a_bogus` / 删 `msToken` / 两个都删 / 删 `x-secsdk-csrf-token` 头，全部仍 `HTTP 200 status_code=0` 返回 10 条。翻页按游标：`cursor` 传上一页返回值（时间戳），`count` 上限 20；传 `cursor=10`（offset 语义）会返回空。
3. **写接口必须真浏览器 + 必须带全套 device query**，两条都是硬要求：
   - `httpx`/`requests` 直连 POST → **403 HTML（`TLB` 边缘）**，与参数无关，是 TLS/指纹层拦的（所以 `curl_cffi` 那条路的替代品就是「用我们自己的 Playwright」）。
   - 页面内 `fetch()` 但**不带 query** → `HTTP 200 {"status_code":0}` 却**什么都不发生**（静默 no-op，最容易误判成成功）。带上跟 list 一致的全套 query（`aid/app_name/device_platform/.../msToken/a_bogus`）后立刻生效。

### 8.3 真机结果（生产号，抖音号 `fengxuan1904` / 昵称 `Jasmine你少吃点`）

| 步骤 | 请求 | 返回 | 回读校验 |
| --- | --- | --- | --- |
| 取关（无 query） | `POST cancel` body `{user_id}` | `200 status_code=0` | **仍在关注** ← 静默失败 |
| 取关（带 query） | 同上 + 全套 query | `200 status_code=0` | **主页按钮「已关注」→「关注」**，全量扫关注列表 0 命中，总数 409→408 |
| 关注 | `POST create` + 全套 query | `200 status_code=0` | 目标立刻回到关注列表第 1 页 |
| 批量取关 | `multi/cancel` 试 `{user_id:[..]}` / `{user_id_list:[..]}` / `{user_ids:".."}` | `status_code=5 参数不合法`（`{user_ids:[..]}` 回 0 但无效） | 键名仍未确认，先别用 |

探测全程**会话没被打死**（读接口一直 200），与 §3.5 在 `www-hj` 上「写失败连带作废登录态」完全不同——creator 后端失败只是失败。节奏上 3 次写操作分散在 2 分钟内没触发任何限流码（阈值未测）。

### 8.4 落地要点（接进 StreamCap 的话）

- 复用 `app/core/platforms/douyin_login.py` 的持久 profile 起 chromium（同一个 `_profile_dir()` + `LAUNCH_ARGS` + `REAL_UA`），**不需要** `curl_cffi`、`ecdsa`、msToken 铸造、secsdk 签名。
- 每个写操作都要：①拉一次 list 拿句柄 ②`page.evaluate(fetch)` 打写接口（query 从同一次 list 请求里抓 `page.on("request")`）③**回读校验**（`www.douyin.com/user/<sec_uid>` 的 `[data-e2e="user-info-follow-btn"]` 文案，或重新扫 list）。
- `status_code:0` 不代表成功——8.3 第一行就是反例；接口没有幂等/事务语义，必须回读。
- 浏览器会话要复用（一次 profile 启动 + 一次页面加载，批量处理），否则每条都要重新付 ~12s 的页面加载成本。

### 8.5 未解

- `multi/cancel` 的 body 键名（批量取关能省请求数，值得再花 10 分钟试 `{user_list:[..]}`、`{user_ids:[], sec_user_ids:[]}` 之类）。
- 频率阈值：连续取关会被 `15371 操作频率过快` / `444` 拦（GitHub 上同类的经验值是 1.1~1.5s/条，被拦后 30~45s 退避），未实测。
- 取关后 `create` 立刻关注回来会触发对方「新粉丝」通知，做批量清理时别无谓地关注。

## 9. 落地（2026-11-25 已上线）

`app/core/platforms/douyin_write.py` + `POST /api/douyin/following/unfollow` + 关注页「取消关注」按钮。

### 9.1 结构

| 部件 | 干什么 |
| --- | --- |
| `CreatorSession`（模块级单例） | 用 `douyin_login._profile_dir()` + `LAUNCH_ARGS` + `REAL_UA` 起 chromium，打开创作者关注页，`page.on("request")` 抓 list 请求的 device query，之后所有请求都在这个页面上下文里 `fetch` |
| `resolve_user_id(nickname, avatar)` | 翻 creator 关注列表建映射（`aweme-avatar/<key>` → `user_id`，昵称兜底），缓存 300s；找不到就报 `target_not_found`，**绝不猜** |
| `cancel_follow(user_id)` | 带全套 query POST `relation/following/cancel/`，写前至少间隔 1.5s；`status_code` 非 0 直接报错 |
| `is_following(sec_uid)` | 开第二个 tab 去 `www.douyin.com/user/<sec_uid>`，读 `[data-e2e="user-info-follow-btn"]` 文案（15s 超时） |
| `unfollow()` | 上面三步串起来，回读 `True` → `verify_failed`；读不到 → `verified:false`（前端提示去主页核实） |

### 9.2 几个必须记住的坑

- **`GOTO_TIMEOUT` 写成秒会变成 60 毫秒**：Playwright 的 `timeout` 是毫秒，第一次验证就是 `Page.goto: Timeout 60ms exceeded`。
- **登录墙 URL 不变**：没登录时页面**停在** `/creator-micro/data/following/following`，只有正文里出现「扫码登录 / 密码登录」，所以每 1s 探一次正文，别等满 40s（实测 42s → 3.7s）。
- **profile 单例锁**：取关的常驻浏览器和扫码登录浏览器用同一份 profile，`start_login` 前要 `reset_session()`，登录进行中取关直接回 409 `err.douyinLoginInProgress`；反过来 launch 也带一次 2s 重试。
- **creator 列表没有 uid**：只有 `user_id`（加密句柄，形如 `@j/dp7bJMT0//sshS8r…`，跨会话稳定但对不上 sec_uid）和 `avatar`，所以匹配只能靠头像 ID；`total` 字段叫 **`total_num`**（写成 `total` 会恒为 None）。
- **写操作必须丢线程**：整段 `await asyncio.to_thread(write_api.unfollow, ...)`，浏览器同步 API 不能进事件循环。

### 9.3 生产验证（2026-11-25，抖音号 fengxuan1904 / Jasmine你少吃点）

| 层 | 结果 |
| --- | --- |
| 单测 `tests/test_douyin_unfollow.py` | 10 passed（头像键/目标匹配/query 清洗/接口错误码 6 种/无 Cookie/成功后清缓存）；全量 412 passed |
| 本机隔离实例（端口 6140，假 Cookie） | 4/4：无 Cookie 400、缺参 422、无登录态 400 `err.douyinLoginRequired`（3.9s，不 500 不挂死） |
| 生产 HTTP（真号） | 关注回来（creator `create`，total 408→409）→ `POST /api/douyin/following/unfollow` **200 `verified:true` 29.6s**（冷启）→ 复核 www `total=408`、creator 扫描 0 命中；热会话下「查无此人」 404 `err.douyinUnfollowTargetMissing` 仅 6.9s |
| 生产 UI（Playwright 打 6006） | 4/4：切到「全部关注」tab → 行出现 → 点击 → 确认框带昵称 → toast「已取消关注「Jasmine你少吃点」」→ 行消失，截图 `/tmp/ui_unfollow.png` |
| 副作用检查 | 取关后监控 56 条原样、关注列表缓存按预期作废（`following.get_store().reset()`），容器日志无 Traceback |

最终状态：`fengxuan1904` 未关注（两侧一致），关注总数 408。

## 10. 反向操作：真「关注」（2026-11-27）

推荐直播列表里的人多数还没关注，卡片上加了「关注」按钮。**关注不走接口，走点页面按钮**：

- www 侧的 `POST /aweme/v1/web/commit/follow/user/` 要 Argus 的 `x-secsdk-web-signature`（缺了就是 403 `Blocked by ArgusSecurityPlugin Uifid Not Found`），即便在页面里 `fetch` 也一样要过签名——所以 `CreatorSession.follow()` 是**新开一个 tab 打开 `https://www.douyin.com/user/<sec_uid>`，点页面自己的 `[data-e2e="user-info-follow-btn"]`**，前端自己带全套签名，最稳。
- 和取关一样不信返回值：只认按钮文案变成「已关注 / 互相关注 / 相互关注」（`FOLLOWED_LABELS`）。已经是关注态直接 `True` 不点；点完 15s 内文案不变返回 `False`（前端提示"稍后自行核对"）；无按钮时按正文区分 `rate_limited`（滑块）与 `login_required`（扫码登录墙）。
- 链路：`POST /api/douyin/following/follow {sec_uid, nickname}` → `_FOLLOW_ERROR_CODES`（`target_not_found` → 404，`upstream_error` → 502 `err.douyinFollowFailed`）→ 成功后 `following.get_store().reset()`（关注总数变了，游标链缓存作废）。
- 验证：`/tmp/verify_follow_dom.py` 起本地假页面（复刻抖音的 DOM 契约）用真 Playwright 跑 5 例——点完变已关注 → True、已是关注态 → True 且不点、点完不变 → False、登录墙 → `login_required`、滑块 → `rate_limited`，全 PASS。真抖音主页没点（会真关注一个真人）。

### 10.1 生产 500 的真凶：Playwright 同步对象跨线程（greenlet）

2026-11-25 上线后，生产上点「取消关注」偶发 `500`：

```
File "/app/app/core/platforms/douyin_write.py", line 390, in unfollow
  ... _greenlets.py line 117 in wait_for_future
greenlet.error: Cannot switch to a different thread
```

原因是 `await asyncio.to_thread(write_api.unfollow, ...)` 落到的是**默认线程池里不确定的一条线程**，而 Playwright 的同步对象（`sync_playwright().start()` 起的 dispatcher greenlet）**绑死在创建它的那条线程**上：第一次调用在 A 线程创建会话，第二次落到 B 线程就会 `Cannot switch to a different thread`。`_SESSION_LOCK` 是 RLock，只护"谁来创建会话"，不护"在哪个线程用它"。

修法：新增 `app/core/platforms/browser_thread.py`——`ThreadPoolExecutor(max_workers=1, thread_name_prefix="douyin-browser")`，提供 `run_sync()` / `run_async()`，所有同步浏览器调用（取关 / 关注 / `reset_session`）都排队到这一条独占线程；`CreatorSession._launch()` 记下 `threading.get_ident()`，`_ensure_page()` 开头校验，发现换了线程就 `close()` 重建（自愈，老路径也不会再炸）。

验证：`/tmp/verify_browser_thread.py` 用真 Playwright 复现——两个独立线程池先后碰同一个 Playwright 对象 → `error: Cannot switch to a different thread`（复现成功）；改走 `browser_thread.run_async()` 两次 → 都成功且线程 id 相同。3/3 PASS。

## 11. 「全部关注」搜索（2026-11-27）

抖音**没有**"搜我关注的人"的接口，所以搜索是**本地过滤 + 继续翻游标链**：

- `GET /api/douyin/following?q=xxx`（`q` 最长 64，服务端 `_normalize_query` 去空白 + 转小写）；匹配 `nickname` / `unique_id` / `short_id` / `sec_uid` 的子串。
- 搜索模式下每次请求最多再翻 `FOLLOWING_WALK_MAX` 页（平台节奏预算有限），够填满本页就停；**`matched_total` 只统计"已翻过的页"里的命中数**，所以 `has_more=true` 时前端显示 `N+ 人`（`following.searchResultMore`），翻到底才是精确值。
- 已加载过的页命中缓存，重复搜同一个词不再出网（`tests/test_douyin_follow_and_search.py::test_store_search_matches_loaded_pages_without_refetch` 守着）。
