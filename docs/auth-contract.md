# 认证契约

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/auth/register` | 开放注册的**第一步**：返回 `202` 与 `{ongoing}`，**不建立会话、也不建账号** |
| `POST` | `/api/v1/auth/register/verify` | 开放注册的**第二步**：凭邮件链接里的令牌核销占位并建号，`204` |
| `POST` | `/api/v1/auth/login` | 成功返回 `200`，经 Cookie 下发会话凭据 |
| `POST` | `/api/v1/auth/logout` | `204`；服务端删除会话并清除 Cookie |
| `GET` | `/api/v1/auth/me` | 当前登录用户；未登录返回 `401` |
| `POST` | `/api/v1/auth/password` | 凭原口令改密，`204` |

注册刻意不自动登录："注册"与"获得会话"是两件明确的事。

## 注册分两阶段

```
POST /auth/register       -> 202 + {ongoing}   建立待验证占位 + 发出验证邮件
                             账号**此时不存在**
POST /auth/register/verify -> 204               删除占位 + 创建账号（同一事务）
                             账号在**这一刻**才出现
```

**为什么不是"先建账号、再验证邮箱"。** 后者会给每一个写错邮箱的注册留下一个永远
无法验证的账号 —— 它占着用户名、可能被用来登录、还得靠人工清理。两阶段把"邮箱写
错"变成一个**什么都没发生**的结果，重来一次即可。

**邮箱是必填的**，且归一化后唯一。没有"仅凭用户名注册"的降级路径：验证的对象就是它。

### 占位保留用户名与邮箱

占位存续期间 `username` 与 `email` 都被锁住（各自唯一）。冲突在**提交那一刻**就暴露，
而不是等用户填完一切、点开链接才失败。

两种冲突**分得清**，因为下一步完全不同：

| 占用者 | `error.code` | 用户该做什么 |
|---|---|---|
| 真实账号 | `username_taken` / `email_taken` | 换个名字，或去登录 / 找回 |
| 另一条占位 | `registration_pending`（`fields` 指出是哪一个） | 查收邮件，或等它过期 |

只说"已占用"会把第二种情形里的用户送去一个**根本不存在账号**的登录页。

### 有效期与清理

占位有效 **10 分钟**（`PENDING_REGISTRATION_TTL_SECONDS`），远短于凭据找回链接的
24 小时 —— 它锁着两个命名空间，不该占太久。

**清理是承重结构，不是卫生工作。** 唯一性由唯一索引保证，而**索引不认时间**：一条
过期的占位在被真正删除之前会一直占着那个用户名和邮箱。因此删除做在两处：

- **注册请求时**先删掉与之冲突的过期行 → 预留**到期即刻释放**，用户不必等后台任务
- **janitor 兜底**删掉全部过期行 → 没人注册时也不会无限堆积

### 核销

以**删除占位本身作为 CAS**，并与创建账号处于**同一事务**：

```sql
DELETE FROM pending_registrations
 WHERE id = ? AND expires_at > now AND token_hash = ?
-- rowcount = 1 -> 我拿到了，继续建号；= 0 -> 已被别人核销或已过期
```

一条语句内完成"校验 + 占用"，与配额 CAS 同一形状，任何隔离级别、任何数据库都成立。
并发核销同一链接时至多一个成功，其余拿到明确错误而不是内部错误。

同事务带来一个关键性质：建号失败（例如占位期间有人抢注了该用户名）时整体回滚，
**占位仍在**，链接在有效期内还能重试 —— 否则用户会既没建成账号又丢了凭据。

### 不引入 6 位验证码

曾考虑邮件里同时给验证码与链接。但那会连带引入：短码必须用 HMAC 摘要（6 位数字裸
哈希一秒反查完）、尝试次数上限、以及一个匿名可爆破的端点。只留链接则这些都是多余
的 —— 256 位随机令牌无法爆破，一次一用、十分钟过期。

**少一个凭据，少一整类要写对的防护。**

### 重入

同一对用户名与邮箱再次提交注册时，响应标记 `ongoing: true`，**不新建占位、不重发
邮件**。前端据此把文案从"邮件已发送"改成"我们已经发过一封" —— 否则用户会去邮箱里
找一封并不存在的新邮件。

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
