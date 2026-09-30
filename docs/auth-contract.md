# 认证契约

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/auth/register` | 开放注册，成功返回 `201` 与用户信息，**不建立会话** |
| `POST` | `/api/v1/auth/login` | 成功返回 `200`，经 Cookie 下发会话凭据 |
| `POST` | `/api/v1/auth/logout` | `204`；服务端删除会话并清除 Cookie |
| `GET` | `/api/v1/auth/me` | 当前登录用户；未登录返回 `401` |
| `POST` | `/api/v1/auth/password` | 凭原口令改密，`204` |

注册刻意不自动登录："注册"与"获得会话"是两件明确的事。

## 会话凭据的传输

### Cookie（浏览器路径）

```
Set-Cookie: cea_sid=<token>; HttpOnly; SameSite=lax; Path=/; Max-Age=604800
```

| 属性 | 值 | 理由 |
|---|---|---|
| `HttpOnly` | 恒开 | **JS 读不到，因此 XSS 偷不走**。这是能从 Bearer 改为 Cookie 的前提——桥接层不需要读令牌（见 `docs/bridge-protocol.md`） |
| `SameSite` | `lax` | 跨站 POST 不携带 Cookie，封掉经典表单 CSRF |
| `Secure` | 生产恒开 | 开发走 http 时经 `SESSION_COOKIE_SECURE=false` 关闭 |
| `Path` | `/` | — |
| `Max-Age` | `SESSION_TTL_SECONDS` | 服务端 `expires_at` 才是权威 |

**响应体中绝不出现凭据。** 登录响应只含用户信息。

### Authorization 头（等价路径）

```
Authorization: Bearer <token>
```

与 Cookie 完全等价，供脚本、测试与非浏览器客户端使用。两者同时存在时 **Cookie 优先**。

## 为什么用 Cookie 而不是 localStorage

内存 / `sessionStorage` / `localStorage` 对 XSS **一样脆弱**，差别只是失窃后的存活期。HttpOnly Cookie 是唯一脚本读不到的。

改用 Cookie 需要补 CSRF，而三重防护叠加后基本封死，其中第三条是沙箱隔离已经付过的成本：

1. `SameSite=Lax`
2. 变更类请求强制 `application/json`（跨站表单发不出这个类型）
3. `/api/**` 不返回任何 CORS 头（跨站 AJAX 连预检都过不去）

## 会话失效的触发条件

**每次请求都校验**有效期、撤销状态与账号启用状态。少最后一项，"停用账号"会一直
拖到会话自然过期才生效。

| 触发 | 效果 |
|---|---|
| 登出 | 该会话被删除 |
| 口令修改 | 除发起改密的会话外，该用户全部会话失效 |
| 账号被停用 | 全部会话失效，且后续请求立即 `401` |
| 角色变更 | 全部会话失效（否则降级后的用户在旧会话里仍有管理权限） |
| 超过 `expires_at` | 拒绝 |

## 口令与令牌用两种不同的哈希

| 用途 | 算法 | 理由 |
|---|---|---|
| 用户口令 | **Argon2id** | 口令是低熵的人类输入，必须靠慢哈希与内存硬度抗离线爆破 |
| 会话凭据、验证与重置凭据 | `secrets.token_urlsafe(32)` + SHA-256 摘要入库 | 令牌已是 256 位随机，爆破不可行；而会话**每个请求都要校验**，用 Argon2 会给每个请求平白加上几十毫秒 CPU |

一句话记法：**口令要"算得慢"，令牌要"猜不到"**。

选 `argon2-cffi` 而非 `bcrypt` / `passlib`：bcrypt 在 72 字节处静默截断口令，
长口令短语尾部熵丢失且不报错；`passlib` 事实上已停止维护，其 bcrypt 后端在
bcrypt ≥ 4.1 上会因 `__about__` 被移除而直接崩。

口令参数升级后，下次成功登录时经 `needs_rehash` 透明重哈希，用户无感。

## 防账号枚举

| 情形 | 响应 |
|---|---|
| 账号不存在 | `401` `invalid_credentials` |
| 口令错误 | `401` `invalid_credentials`（与上者**完全一致**） |
| 口令正确但账号被停用 | `403` `account_disabled` |

前两者必须逐字节一致，且账号不存在时仍执行一次等价耗时的哈希校验——响应耗时的
差异本身就是枚举信道。

第三种之所以可以给出明确提示，是因为它发生在**口令校验之后**：只有本来就持有
正确口令的人才会看到，不构成泄露。

## 口令与用户名规则

- 用户名归一化（NFC + 去首尾空白 + casefold）后比较与存储。SQLite 的 `=` 区分
  大小写而 MySQL 默认不区分，不归一化则唯一约束行为会分叉。
- 用户名：3–32 字符，`[a-z0-9]` 开头结尾，中间可含 `. _ -`。
- 保留名（见 `RESERVED_USERNAMES`）拒绝注册。清单必须全小写。
- 口令：8–128 字符。**不强制**大小写/数字/符号组合——那类规则会把人推向
  `Passw0rd!` 这种可预测形态，长度才是有效的强度杠杆。
- 邮箱：可选，归一化后唯一，形状只做最低限度检查（真实性只能靠验证链接确认）。

## 错误码

| `error.code` | 状态 | 含义 |
|---|---|---|
| `validation_failed` | 422 | 字段级错误，`error.fields` 给出 `{字段: 消息}` |
| `invalid_credentials` | 401 | 账号不存在或口令错误 |
| `login_required` | 401 | 需要登录 |
| `account_disabled` | 403 | 口令正确但账号被停用 |
| `forbidden` | 403 | 已登录但权限不足 |
| `username_taken` | 409 | 用户名已被占用 |
| `email_taken` | 409 | 邮箱已被绑定 |
| `rate_limited` | 429 | 认证类端点超过来源维度阈值，带 `Retry-After` |

## 测试对照

| 契约 | 测试 |
|---|---|
| Cookie 属性、响应体不含凭据 | `test_auth.py::TestLoginAndSession` |
| Bearer 等价 | `test_bearer_path_is_equivalent` |
| 会话失效四种触发 | `test_auth.py::TestSessionLifecycle`、`test_deps.py::TestSessionInvalidation` |
| 防枚举 | `test_auth.py::TestAntiEnumeration` |
| 哈希算法分工 | `test_auth.py::TestSecurityPrimitives` |
| 用户名归一化与保留名 | `test_auth.py::TestRegistration` |
| 改密后其他会话失效 | `TestChangePassword::test_other_sessions_are_revoked` |
