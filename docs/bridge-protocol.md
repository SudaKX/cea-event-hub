# 桥接协议 v1

宿主单页应用与活动页之间的通讯契约。**两侧共用同一份类型定义**
（`frontend/src/bridge/protocol.ts`），任何改动都必须先改那里，否则两侧会各自漂移。

## 为什么活动页没有凭据

活动页是组织者自行编写的 HTML，且与宿主同源。如果它持有会话凭据，一旦页面被写坏
或被注入，它就能顶用管理员会话调用**任何**接口，包括管理接口。

因此采用代理模型：活动页只能通过 postMessage 请求**预定义操作**，由宿主用自身
会话代为调用后端。安全收益有三层：

| 机制 | 作用 |
|---|---|
| 沙箱 iframe（不含 `allow-same-origin`） | 活动页碰不到宿主 DOM 与存储 |
| `/api/**` 不返回任何 CORS 头 | 活动页**无法**绕过代理直接调后端（浏览器强制） |
| op 白名单 | 宿主不会成为"用自己会话转发任意路径"的开放代理 |

第三条最关键：如果宿主按活动页给的地址转发，前两条就白做了。

## 沙箱令牌集合

```
sandbox="allow-scripts allow-forms allow-modals allow-popups"
```

**绝不含 `allow-same-origin`。** 同源 iframe 一旦拿到这个令牌，沙箱等于没有：它能
访问 `parent.document`，读到宿主内存里的状态，甚至把自己身上的 sandbox 属性摘掉。

其余令牌的用途：

| 令牌 | 为什么需要 |
|---|---|
| `allow-scripts` | 活动页要跑 JS，也是 SDK 的前提 |
| `allow-forms` | 原生 `<form>` 提交不被拦 |
| `allow-modals` | `alert` / `confirm` / `prompt`（更好的做法是走 `CEA.toast`） |
| `allow-popups` | `target="_blank"` 外链 |

**不给** `allow-top-navigation`（防活动页劫持整页）与
`allow-popups-to-escape-sandbox`（否则弹窗继承不到约束）。

### 沙箱带来的能力损失与替代

不透明源没有本地存储，`history.pushState` 也会抛异常。宿主为每一条都提供了替代：

| 损失 | 替代 |
|---|---|
| `localStorage` / `sessionStorage` 抛错 | `CEA.draft.save/load/clear`（宿主按活动代存，不落后端） |
| `document.cookie` 不可用 | 不需要 —— 活动页本就不该持有凭据 |
| `history.pushState` 抛 SecurityError | 页内多步流程用 hash 路由，或 `CEA.navigate()` |
| `alert` / `confirm` 需 `allow-modals` | `CEA.toast()`（主题统一） |
| 不能 `fetch('/api/...')` | 一切数据经 `CEA.*` 获取 |

## 消息信封

```jsonc
{ "v": 1, "type": "hub:init", "id": "r1", "payload": { } }
```

| 字段 | 说明 |
|---|---|
| `v` | 协议主版本。不一致时宿主明确报错，不带着不兼容的假设继续 |
| `type` | 消息类型 |
| `id` | RPC 关联标识，由活动页生成，宿主原样回传 |
| `payload` | 载荷 |

## 握手

```
活动页                                宿主
  |  event:ready {protocolVersion} -->  |
  |                                     |  校验主版本
  |  <-- hub:init {identity, theme}     |
```

宿主同时监听 iframe 的 `load` 事件重发初始化，因此握手是**幂等**的：重复就绪、
iframe 内部导航、重新加载都不会产生副作用。

### 来源校验：为什么不能用 origin

不透明源下活动页发来的消息，`event.origin` 恒为字符串 `"null"` —— 拿它做鉴权没有
任何意义。两侧都改用**窗口引用**判定：

```
宿主接收：  event.source === iframe.contentWindow     不可伪造
宿主发送：  targetOrigin = "*"
SDK 接收：  event.source === window.parent            不可伪造
```

`targetOrigin: "*"` 在持有凭据的方案里是漏洞；在这里成立，**正是因为 iframe 什么
都不持有** —— 泄露的只有公开活动信息与主题令牌。

## 身份描述符（零凭证）

```jsonc
{
  "loggedIn": true,
  "userId": 7,
  "displayName": "张三",
  "role": "user",
  "clientId": "3f2a…",              // 匿名标识，不是凭据
  "submissionRequiresLogin": false  // 供活动页提前渲染正确状态
}
```

**其中不含任何可用于调用后端接口的值。** 登录态变化时宿主主动下发
`hub:session` 更新。

`clientId` 由宿主产出并持久化（`localStorage`），因为沙箱 iframe 没有存储。
它不是凭据，被 XSS 读走无害，因此可以放在最"不安全"的存储里 —— 这也是它与会话
Cookie 处在完全不同安全等级的原因。

## 操作表

**固定白名单，不是 URL 转发。**

| op | 参数 | 宿主映射到 |
|---|---|---|
| `event.info` | — | `GET /events/{宿主路由的 eventId}` |
| `me.profile` | — | 本地身份描述符（无网络请求） |
| `me.submissions` | — | `GET /me/submissions` |
| `form.submit` | `{payload, kind?, idempotencyKey?}` | `POST .../submissions` |
| `form.submitFiles` | `{payload, files, kind?, idempotencyKey?}` | `POST .../submissions:files` |
| `draft.save` | `{formKey, value}` | 宿主 sessionStorage |
| `draft.load` | `{formKey}` | 同上 |
| `draft.clear` | `{formKey}` | 同上 |

**铁律：活动标识一律由宿主从自身路由取，绝不采信活动页传值。** 否则活动页能借宿主
会话操作别的活动的数据。测试对此有专门断言：即使参数里带了 `event_id`，宿主仍用
自己的。

白名单外的操作返回 `unsupported_op`，且**不发出任何网络请求**。

## 结果与错误

```jsonc
// 成功
{ "v": 1, "type": "hub:result", "id": "r1", "payload": { "ok": true, "data": { } } }

// 失败
{ "v": 1, "type": "hub:result", "id": "r1",
  "payload": { "ok": false, "error": { "code": "quota_exhausted", "message": "…",
                                        "fields": { } } } }
```

**活动页不需要解析 HTTP 状态码**，只需判断 `code`：

| `code` | 含义 | 该做什么 |
|---|---|---|
| `login_required` | 活动要求登录 | 引导登录 |
| `event_closed` | 未发布 / 未开放 / 已截止 | 渲染"已截止"，**停止** |
| `quota_exhausted` | 名额已满 | 渲染"名额已满"，**停止** |
| `rate_limited` | 触发限流 | 提示稍后重试（**唯一值得重试的**） |
| `validation_failed` | 内容不合规 | 用 `fields` 标注到对应输入框 |
| `payload_too_large` | 超出体积上限 | 提示压缩或减少文件 |
| `unsupported_op` | 操作不在白名单 | 这是活动页的 bug |
| `timeout` | 宿主等待超时 | 提示重试 |
| `cancelled` | 请求被取消或 iframe 重载 | 忽略 |

`quota_exhausted` 与 `rate_limited` 的区别是刻意的：前者是终态，后者是"稍后重试"。

## 宿主侧的其它消息

| 方向 | type | 说明 |
|---|---|---|
| 活动页 → 宿主 | `event:resize` | `{height}`，宿主据此调整 iframe 高度 |
| | `event:navigate` | `{to}`，**只接受站内路径**，外部地址被忽略 |
| | `event:title` | `{title}` |
| | `event:toast` | `{level, message}` |
| | `event:error` | `{message}` |
| 宿主 → 活动页 | `hub:upload-progress` | `{requestId, loaded, total}` |
| | `hub:theme` | 设计令牌 |

## SDK 的引入方式

**由托管层自动注入，作者无需记得写。** 内容服务端在返回 HTML 前检查：页面里
已经有 `/sdk/v1/cea.js` 或 `id="cea-sdk"` 的标签就原样返回，否则在 `</head>` 前
补一行：

```html
<script src="/sdk/v1/cea.js" id="cea-sdk"></script>
```

### 为什么必须由服务端做，而不是宿主注入

宿主**够不到** iframe 内部：不透明源下 `iframe.contentDocument` 抛
`SecurityError`，它无法替活动页插入 `<script>`。postMessage 也自举不了 —— 要
"收到地址再加载"，活动页得先有代码在监听消息，而那段代码本身得先存在。所以能
去掉这个约定的位置只有托管内容的那一层。

这不违反"宿主不得向 iframe 注入脚本"那条约束：注入发生在内容服务端，返回的是
另一份字节；宿主仍然碰不到 iframe 的文档，沙箱边界没有任何变化。

### 用 `id` 而不是 `class`

两者都是全局属性、都合法。选 `id` 是因为语义更准：一份文档里只该有一个桥接
脚本，`id` 的"唯一"正好对上 `class` 的"可多个"。它同时给活动页一个稳定的抓手 ——
`document.getElementById('cea-sdk')`。

### 幂等与开关

活动页自己写了引用就不再注入，两种写法都能工作。开关是 `CONTENT_SDK_INJECT`
（默认开），关掉即回到"必须显式引用"。注入只作用于 `.html` / `.htm`，其他文件
原样返回。

## 就绪诊断仍然保留

注入让"忘了写"几乎不可能发生，但宿主仍保留 5 秒未就绪的诊断。它现在覆盖的是
另外两种情况：

- SDK 文件本身没构建（`dist/sdk/v1/cea.js` 不存在，例如没跑过 `npm run build:sdk`）
- 活动页自己的脚本在 SDK 加载完成之前抛错，中断了后续执行

诊断比任何文档都管用：没有它，作者看到的是一片空白或固定高度的 iframe，完全
无从判断哪里出了问题。

## 测试对照

| 契约 | 测试 |
|---|---|
| 信封与版本守卫 | `bridge/protocol.spec.ts` |
| 来源校验、未知类型忽略 | `host.spec.ts :: 来源校验` |
| 握手幂等、版本不匹配、缺失诊断 | `host.spec.ts :: 握手` |
| 描述符零凭证 | `host.spec.ts :: 身份描述符` |
| 白名单、eventId 不可伪造 | `host.spec.ts :: 操作白名单` |
| 登录短路不发请求 | `host.spec.ts :: 提交前登录短路` |
| 超时、取消、重载清理 | `host.spec.ts :: 超时与清理` |
| 错误码映射 | `host.spec.ts :: 错误码映射` |
