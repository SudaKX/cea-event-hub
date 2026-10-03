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
| `localStorage` / `sessionStorage` 抛错 | `CEA.storage.*`（宿主按活动代存，键由宿主拼成，不落后端） |
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
| `storage.save` | `{key, value}` | 宿主 localStorage，键为 `cea.storage:{eventId}:{key}` |
| `storage.load` | `{key}` | 同上 |
| `storage.remove` | `{key}` | 同上 |
| `storage.clear` | — | 清空本活动的全部键 |

**铁律：活动标识一律由宿主从自身路由取，绝不采信活动页传值。** 否则活动页能借宿主
会话操作别的活动的数据。测试对此有专门断言：即使参数里带了 `event_id`，宿主仍用
自己的。

**储存的 key 所有权同样在宿主。** 活动传的 `key` 只是一个命名空间片段，宿主把它和
活动标识拼成真正的 localStorage 键。活动因此无法指到别的活动，也无法触碰宿主自己
的键。片段形态不合规时返回 `storage_key_invalid`，**不回落** —— 回落会让两个不同
的槽位撞在一起互相覆盖。

本活动所有储存数据的总量上限是 `STORAGE_MAX_LENGTH`（4096 字符，按 JSON 序列化
后计算），超出返回 `storage_too_large`，**不截断**。按活动封总量是因为 localStorage
是同源共享资源：一个活动页写爆它，同源下所有页面都会一起失败。

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
| `storage_too_large` | 本地储存总量超 4096 字符 | 精简要存的内容；草稿类可以忽略这个错误 |
| `storage_key_invalid` | 储存 key 不合规 | 这是活动页的 bug，改 key |
| `unsupported_op` | 操作不在白名单 | 这是活动页的 bug |
| `timeout` | 宿主等待超时 | 提示重试 |
| `cancelled` | 请求被取消或 iframe 重载 | 忽略 |

`quota_exhausted` 与 `rate_limited` 的区别是刻意的：前者是终态，后者是"稍后重试"。

## 宿主侧的其它消息

| 方向 | type | 说明 |
|---|---|---|
| 活动页 → 宿主 | `event:navigate` | `{to}`，**只接受站内路径**，外部地址被忽略 |
| | `event:title` | `{title}`，写入 `document.title` |
| | `event:toast` | `{level, message}`，宿主渲染成通知。见下 |
| | `event:error` | `{message}` |
| 宿主 → 活动页 | `hub:upload-progress` | `{requestId, loaded, total}`。**载荷里带 requestId**，因为它是过程中的推送而不是对某条消息的应答；SDK 按这个 id 找到发起该请求的回调 |
| | `hub:theme` | 设计令牌 |

### `CEA.toast` 的边界

提示渲染在 iframe **之外**、宿主的界面里 —— 这是不受信内容唯一能写到宿主界面上的
东西。因此桥接层做三道收敛，**活动页会拿到什么就按什么渲染，调用方不必再校验**：

| 收敛 | 规则 | 为什么 |
|---|---|---|
| 语气 | 只认 `ok` / `info` / `error`，其余一律 `info` | 提示本身无害；为多写一个字就把整条丢掉，只会让作者摸不着头脑 |
| 长度 | 截断到 200 字 | 通知是浮层，几千字会把整屏占满 |
| 频率 | 10 秒内最多 5 条，超出丢弃 | 真正的影响不是"吵"，而是**宿主自己的提示被挤掉** —— 通知栈有显示上限，活动页刷屏会把"已保存"顶出去 |

**刻意不做的：不给来自活动页的通知加视觉标记。** 本文档把 `CEA.toast` 定位成替代
`alert()` 的正规做法（见上面的对照表），给它打上"可疑"的标签会让正常用法跟着显得
可疑；而没有可点内容、又会自动消失的纯文本，风险仅限于"看一眼"。

正文不是字符串（活动页可以发任何东西过来）时整条忽略。

## SDK 的引入方式

**由托管层自动注入，作者无需记得写。** 内容服务端在返回 HTML 前检查页面里有没有
`id="cea-sdk"` 的 `<script>`：**没有就在 `</head>` 前补一行，有就原样返回。**

```html
<script src="/sdk/v1/cea.js" id="cea-sdk"></script>
```

### 判断只看 `id`，不看 `src`

路径会变（`/sdk/v1/` → `/sdk/v2/`）。按 `src` 判断的话，SDK 一换路径所有老页面都会
被判定成"没有引用"，于是被插入新标签、SDK 加载两次。`id` 是稳定的。

**不做迁移。** 早期指南里的 `<script src="/sdk/v1/cea.js"></script>`（没有 id）不特殊
处理 —— 它被判定成"没有引用"，因而得到一个注入的新标签，结果是 SDK 加载两次。这是
刻意接受的代价：与其在代码里长期维护一条迁移路径，不如让老页面显式改过来（示例内容
已经改好）。有一个测试把这个行为钉住，免得日后被当成 bug 来"修"。

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

### 定位方式：HTMLParser，不是正则

`</head>` 可能出现在**注释**里或**内联脚本的字符串**里。正则会把插入点放到那里面，
标签等于没插 —— SDK 不加载，而页面看上去一切正常。`HTMLParser` 把注释交给
`handle_comment`、把 `<script>` 内容按 CDATA 处理，两种情况都不会误判。

也不用 XML 解析：活动页是手写 HTML5（`<br>`、未加引号的属性值都是合法 HTML 但非法
XML），而且树模式必须把文档序列化回去 —— 那会重写作者的文件。本模块只插入一个
子串，从不改写其它字节。

### 开关

`CONTENT_SDK_INJECT`（默认开），关掉即回到"必须自己写"。`CONTENT_SDK_PATH` 可改
SDK 路径。注入只作用于 `.html` / `.htm`，其他文件原样返回。

## 就绪诊断仍然保留

注入让"忘了写"几乎不可能发生，但宿主仍保留 5 秒未就绪的诊断。它现在覆盖的是
另外两种情况：

- SDK 文件本身没构建（`dist/sdk/v1/cea.js` 不存在，例如没跑过 `npm run build:sdk`）
- 活动页自己的脚本在 SDK 加载完成之前抛错，中断了后续执行

诊断比任何文档都管用：没有它，作者看到的是一片空白，完全无从判断哪里出了问题。

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
