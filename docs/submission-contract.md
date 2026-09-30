# 提交契约

活动页提交信息与文件的完整约定。**活动页不直接调用这些端点**——它经桥接层由宿主
代发（见 `docs/bridge-protocol.md`）。这份文档描述的是宿主与后端之间的契约。

## 两个端点

### `POST /api/v1/events/{event_id}/submissions` — 纯字段

```
Content-Type: application/json          ← 强制
Idempotency-Key: 8f3a...                ← 可选

{ "name": "张三", "grade": "1" }        ← 请求体本身就是提交内容，没有信封
```

**强制 `application/json`** 同时是一条免费的 CSRF 防线：跨站表单只能发
form-urlencoded / multipart / text-plain，发不出这个类型。非该类型返回 `415`。

### `POST /api/v1/events/{event_id}/submissions:files` — 字段 + 文件

```
Content-Type: multipart/form-data

payload = {"name":"张三","grade":"1"}   可选，JSON 字符串
files   = <文件>                         可重复，可多个
```

**这是超集。** 需要"字段 + 文件"的活动页**只发这一个端点**，不要分两次调用——
否则同一次提交会在管理端显示成两条记录。SDK 会按有无文件自动选择，活动作者视角
永远是一次调用。

### 查询参数（两个端点通用）

| 参数 | 说明 |
|---|---|
| `kind` | 可选的自由标签，见下方规则 |
| `client_id` | 匿名标识，由宿主附加。**不是凭据**，仅用于分组 |

## `kind` 规则

`kind` 不做语义校验，但它会进入文件路径，因此**必须**受限：

- 允许 `[a-z0-9_-]`，长度 1–64
- 不符合（含大写、点号、路径分隔符、超长）时**回落到 `_default`**，而不是拒绝整次提交
- 该值**不影响路径的任何上级目录**

## 幂等与去重

两条独立的机制，**都在配额检查之前**执行：

| 机制 | 触发条件 | 作用域 |
|---|---|---|
| 幂等键 | 请求头 `Idempotency-Key` 相同 | 活动内（不同活动可复用同一个键） |
| 内容去重 | 同一提交者、窗口内、内容摘要相同 | 活动内，窗口由 `DEDUP_WINDOW_SECONDS` 控制 |

命中任一机制时返回 `201` 且 `deduplicated: true`，`submission.id` 指向首次创建的记录，
**不新建行、不消耗配额**。

顺序很关键：去重若在配额之后，一个反复重试的客户端会在自己已经被去重的情况下把
名额撞满。

## 提交者标识

| 情形 | `submitter` |
|---|---|
| 已登录 | `u:{user_id}`（由服务端会话推导，客户端传值无法覆盖） |
| 匿名且带 `client_id` | `a:{client_id}` |
| 匿名且未带 | `a:unknown` |

**匿名标识不是凭据。** 它可以被伪造也可以被清除，因此不能用于鉴权。直接后果是：
**匿名用户没有"我的提交历史"**，读取历史必须登录。

## 存储布局

```
字节    data/{event_id}/{kind}/{yyyy}/{mm}/{uuid}{ext}     私有，绝不静态暴露
元信息  submission_files 表（原名、大小、sha256、嗅探类型、存储状态）
内容    submissions 表（payload、payload_hash、幂等键、状态）
```

原始文件名**只作为元信息**，绝不参与路径构造。下载时经鉴权端点，响应强制
`Content-Disposition: attachment` 与通用二进制类型。

## 错误码

| `error.code` | 状态 | 含义 | 客户端该做什么 |
|---|---|---|---|
| `login_required` | 401 | 活动要求登录 | 引导登录 |
| `event_closed` | 403 | 未发布 / 未到开放时间 / 已截止 | 渲染"已截止" |
| `quota_exhausted` | 409 | 名额已满 | 渲染"名额已满"，**不再展示表单** |
| `rate_limited` | 429 | 触发限流 | 提示稍后重试（唯一值得重试的） |
| `validation_failed` | 422 | 内容不合规，`error.fields` 给出字段级信息 | 标注到对应输入框 |
| `payload_too_large` | 413 | 内容或文件超出体积上限 | 提示压缩或减少文件 |
| `unsupported_media_type` | 415 | 非 `application/json` | 修正请求类型 |
| `not_found` | 404 | 活动不存在 | 停止 |

**`409` 与 `429` 的区别是刻意的**：前者是终态，后者是"稍后重试"。用 `429` 表示
名额已满会诱导客户端重试一个永远不会成功的请求。

## 体积与数量上限

| 配置项 | 含义 |
|---|---|
| `MAX_PAYLOAD_BYTES` | 规范化序列化后的内容上限 |
| `MAX_UPLOAD_BYTES` | 单文件上限 |
| `MAX_FILES_PER_REQUEST` | 单次请求文件数上限 |
| `MAX_REQUEST_BYTES` | 整个请求体上限（另有一道 nginx `client_max_body_size`） |

超限请求**不留下任何已落盘文件**：已写入的部分会被清掉，数据库那侧整体回滚。

## 配额

- 可匿名提交的活动默认封顶 **4096 条**（`MAX_SUBMISSIONS_PER_EVENT_ANON`）
- 需登录的活动默认**不限额**（`MAX_SUBMISSIONS_PER_EVENT_AUTHED`）
- 活动可用 `max_submissions` 覆盖；`0` 是合法值，表示谁都不能提交
- 达到上限返回 `409` `quota_exhausted`
- **管理员删除提交会释放名额**，这是满额活动唯一的自救手段

配额由单语句 CAS 维护，并发提交不会突破上限；写入失败时名额随事务回滚。

## 相关文档

- 活动页接入方式与能力边界：`docs/bridge-protocol.md`
- 匿名标识与身份描述符：同上一份
