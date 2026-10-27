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