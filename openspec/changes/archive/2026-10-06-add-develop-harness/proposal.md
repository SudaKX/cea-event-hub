# Proposal

## Why

活动页是手写单文件 HTML，它与宿主之间只有一条 postMessage 桥接。这条链路要真正被执行，
需要四个条件同时成立：一个**已发布**的活动、一份**已投放**的内容、一个沙箱 iframe、
一个真后端。目前这四个条件没有一个能由开发者随手凑齐：

- 本地调试要把页面放进 `content/{event_id}/`，而那是**投放产物**目录 —— 它由
  `.gitignore` 排除、不入库，且下一次 zip 投放会把它整体替换掉（`_swap_in` 会先改名
  成 `.previous` 再删掉）。作者的工作台恰好是部署的目标位置。
- 活动必须是 live：`getPublicEvent` 对 draft 返回 404，草稿活动连 `CEA.event()` 都拿不到。
- 于是活动页的宿主交互只能在**投放之后**才被验证，而那时内容已经在线上了。

代价最集中的是桥接的**错误分支**：`quota_exhausted`、`event_closed`、`login_required`、
`submitter_quota_exhausted`、`timeout`、`cancelled`。它们要么难造（`timeout` 需要一个慢端点），
要么要改数据库（把名额设成 0），要么根本没有触发的办法。结果是这些分支在活动页里
基本处于"写完了但没人跑过"的状态，而它们恰恰是出问题时用户唯一会看到的东西。

秋季招新已经排上，活动页只会变多。每多一页，"改一行要投放一次才能看"的成本就再乘一遍，
所以现在补这个缺口。

## What Changes

- **新增 `events/`**：活动页的**源码**目录，入库。与 `content/`（投放产物，不入库）职责分开，
  不再让作者在部署目标位置上改稿。
- **后端在开发模式下把 `events/` 挂到 `/draft/**`**，行为与 `/content/**` 一致 ——
  通配 CORS 头与 SDK 自动注入都照旧，因此草稿页与线上页面对宿主而言没有区别。
  仅 `APP_ENV=development` 时挂载。
- **新增 `DevelopView` 调试台**（仅开发构建存在的路由 `/develop`）：
  - iframe 来源由 query params 给定，限定站内路径；
  - 宿主行为用**真的 `BridgeHost`**（与生产同一份），"调试"体现在面板的观测与注入上，
    而不是另写一个宿主——后者会与真宿主漂移，而漂移正是要防的那个问题；
  - 类 devtools 面板：双向消息日志、握手与待决状态、宿主→iframe 的可用动作按钮；
  - 可**逐 op 选择性拦截并抢先应答**，用来造真后端造不出来的分支（`timeout`、
    `unsupported_op`、`validation_failed`）。默认全关，伪造过的请求在日志里打标记。
- **开发模式下确保存在一个专用开发活动**（启动时补齐，与既有的管理员引导同一形状），
  供 `?event=` 缺省使用；`?event=` 仍可指向任意已发布活动以验证特定配置。
- **生产构建不含 `/develop` 路由与该组件**。

## Capabilities

### New Capabilities

- `dev-harness`: 开发模式的集成测试设施。涵盖三件事：`events/` → `/draft/**` 的草稿内容挂载
  （仅开发模式、行为与 `/content/**` 一致）、专用开发活动的启动补齐、以及 `/develop`
  调试台（来源约束、双向观测、可选的响应伪造）。放在同一个能力里是刻意的：这三件事共同的
  契约是"**只在开发模式存在**"，分散到三个能力去写会让这条边界变得需要拼凑才能看清。

### Modified Capabilities

- `web-app-shell`: 保留前缀清单新增 `draft` 与 `develop`。该要求逐个枚举了清单内容，
  两边不一致正是它要防的情形（"只加路由不登记，那一页会被当成活动标识且不报任何错"）。
  代价是活动 ID 不能再叫 `draft` 或 `develop` —— 与 `content`、`data`、`sdk` 已经付出的代价同类。

> `event-content-hosting` **不动**：`/content/{event_id}/{path}` 的形状、缓存与 CORS 语义
> 都不变，`/draft/**` 是新增的开发专用路径而不是它的替代。把草稿挂载写进 `dev-harness`
> 而不是这里，是为了让"只在开发模式存在"这条边界只出现在一处。

## 本期不做

- **不给 `BridgeHost` 加可注入的 API 端口**（`loadEvent` / `submit` / `mySubmissions`）。
  伪造走 wire 层就够，生产代码保持零改动。留作后续：真造不出来的分支只剩 `payload_too_large`
  之类少数几条，不值当为此在生产类上开口子。
- **不做生产环境可用的 `/draft` 或 `/develop`**，也不提供"临时打开"的开关。
- **不把 build + grep 的检查接进 CI**。本期只交付一个可复跑的脚本作为验收证据；门禁化留待
  这套东西稳定之后再说。
- **不做 `events/` → 线上内容的快捷投放**。投放仍然只走管理台 zip，`events/` 纯粹是源码目录；
  不引入第二条投放路径，否则"线上是什么"就不再能从单一来源推断。
- **不做 `events/` 与 `content/` 的自动同步**。两者职责不同，同步会重新把它们混成一个。
- **不改 `/content/**` 的路径、缓存策略与 CORS 语义。**
- **不做多页并行调试**：一次一个 `?src=`。
- **不做 `events/` 下的活动配置清单**（在目录里声明标题/名额/是否需登录）。本期开发活动由
  后端种子补齐，配置改动走管理台。

## Impact

**后端**
- `backend/app/core/config.py`：新增 `events/` 目录配置；开发模式判据复用既有 `APP_ENV`。
- `backend/app/main.py`：`APP_ENV == "development"` 时挂载 `/draft`，**复用
  `ContentStaticFiles`**（换成裸 `StaticFiles` 会丢掉 SDK 注入，症状是"活动页连不上宿主"
  而根因在挂载方式）。
- 新增 `backend/app/services/dev_seed.py`：`ensure_dev_event()`，由既有的
  `run_startup_tasks()` 调用，形状对齐 `ensure_bootstrap_admin()`。
- `backend/.env.example`：补上新配置项。

**前端**
- 新增 `frontend/src/views/develop/DevelopView.vue` 与调试面板组件。
- `frontend/src/router/index.ts`：开发构建下注册 `/develop`；保留前缀加 `draft`、`develop`。
- `frontend/vite.config.ts`：新增 `/draft` 代理（不加会被 history fallback 接住，
  返回 SPA 外壳而不是 404）。
- `frontend/src/bridge/protocol.ts`：只读引用，不改协议。

**部署**
- `deploy/nginx/cea-event-hub.conf`：生产对 `/draft/` 明确 `return 404`，理由与既有的 `/data/`
  同一条 —— 生产下不能返回一个 200 的应用外壳。

**文档**
- 新增 `docs/dev-harness.md`。
- `docs/event-page-guide.md`：「本地调试」一节现在教人把页面放进 `content/{event_id}/`，
  那是投放产物目录；改为指向 `events/<name>/` + `/develop`，并说明两者职责区别。

**数据**
- 开发库会多一行开发活动（仅开发模式、可重复执行、幂等）。
