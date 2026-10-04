# Proposal

## Why

社团活动目前依赖临时拼凑的静态页 + 群接龙/在线表格收集信息：每个活动都要从零搭页面和收集渠道，信息散落在不同工具里，附件靠聊天软件传递，既无法审核追溯，也没有统一的权限与容量控制。

需要一个统一平台：**活动页仍由各活动自行编写 HTML（保留自由度），但托管、身份、信息收集、文件存储与审核由平台统一承担**。现在动手是因为项目骨架刚初始化（Vue 脚手架、Python venv、OpenSpec、git 均为空壳），尚无业务代码，是从零确立分层与安全边界成本最低的时刻。

## What Changes

- 新建 **FastAPI 后端**与 **Vue3 + TypeScript SPA 前端**，前后端分离；后端采用 repos / services 分层，SQLite + SQLAlchemy 2.x ORM 并自第一个提交启用 Alembic，预留向 MySQL 迁移。
- 后端 API 统一前缀 `/api/v1/`（可配置）；nginx 按 `/api/` 与 `/content/` 前缀反向代理到后端，SPA 静态产物由 nginx 直出。
- 活动网页内容托管于 `/content/{event_id}/`（公开可读）；活动上传文件落 `/data/{event_id}/`（私有，nginx 硬 404 且 FastAPI 不挂载）。
- SPA 路由：`/admin` 为管理台，`/login` 等为保留前缀，其余路径通配到活动页；活动页以 **sandbox iframe** 承载活动内容（不含 `allow-same-origin`）。
- 活动页与宿主通过 **postMessage 桥接协议 v1** 通讯；**iframe 零凭证**，所有后端调用由宿主按 **op 白名单**代理，`eventId` 只由宿主路由提供。
- 用户体系：开放自助注册 + `user` / `admin` 两级权限；会话用 **HttpOnly Cookie**（`Authorization: Bearer` 为等价路径）。
- 活动信息提交端点：JSON 与 multipart 两类，支持幂等去重；文件字节落盘、元信息落 `submission_files`，非文件信息落 `submissions`。
- 提交配额：可匿名提交的活动设活动级条数上限（默认 4096），以 `events.submission_count` 计数器 CAS 实现。
- 限流：**nginx（按客户端 IP）+ FastAPI 应用层（按 IP/用户）** 两层，本期不做数据库限流。
- 前端视觉沿用既有海报的暗色 / 等宽 / 点阵设计语言，并统一为设计令牌。
- **明确不做**（本期范围外）：表单字段契约与 `event_forms` 表、数据库限流表、滑动窗口限流、邮件服务的实际接入（仅预留接口）。

## Capabilities

### New Capabilities

- `event-catalog`: 活动的生命周期（草稿/发布/归档）、管理端增删改、公开目录与详情读取，以及活动级的提交策略字段（是否需要登录、开放/截止时间）与配额状态暴露。
- `event-content-hosting`: 活动网页内容在 `/content/{event_id}/` 的托管与投放，含 zip 上传解压的路径穿越与符号链接防护、内容版本号递增、缓存头策略，以及 `/content` 与 `/api` 刻意不对称的 CORS 策略。
- `submission-intake`: 匿名与登录两种提交接收（JSON 与 multipart 两个端点）、自由 `kind` 标签、payload 结构与体积约束、幂等去重、文件落盘与落库的原子性，以及可匿名活动的条数配额 CAS。
- `attachment-access`: 私有附件的鉴权下载，含强制附件下载头、`/data` 不可静态访问的约束，以及上传者与管理员两类访问判定。
- `embed-bridge`: 沙箱 iframe 的承载约束、postMessage 协议 v1 信封、握手、op 白名单代理、提交前登录短路、超时与取消、上传进度回推、宿主代存草稿，以及身份描述符（零凭证）下发。
- `identity-auth`: 开放注册（用户名归一化、保留名、**邮箱必填**，注册分两阶段 —— 先建立待验证占位、经邮件链接核销后才创建账号）、登录/登出、会话校验与失效传播、修改口令，以及邮箱验证与密码找回（无邮件基础设施时降级为管理员签发一次性令牌）。
- `user-administration`: 管理员对用户的查询、角色提升、停用/启用与签发重置令牌。
- `submission-review`: 管理端查看、筛选、审核提交（状态流转）、下载任意附件，以及删除提交（须同事务递减配额计数器）。
- `request-rate-limiting`: nginx `limit_req` 的 IP 层限流与应用层按 IP/用户的内存限流，含代理头信任边界与 `429` 语义。
- `web-app-shell`: SPA 路由与保留前缀表、管理台外壳、活动页宿主、404 页，以及统一的设计令牌与点阵视觉语言。

### Modified Capabilities

无。项目此前无任何已声明的能力（`openspec/specs/` 为空）。

## Impact

**新增代码与目录**

- `backend/`：FastAPI 应用、`core/`（配置、安全、错误、依赖、端口）、`db/`（base、session、models）、`repositories/`、`services/`、`infra/`（限流与文件存储实现）、`api/v1/`、`alembic/`、`tests/`
- `frontend/src/`：`api/`（axios 客户端）、`stores/`、`bridge/`（协议、宿主、SDK）、`views/`（登录、注册、重置、管理台、活动页、404）、`components/`、`styles/`
- `content/{event_id}/`、`data/{event_id}/`（运行时数据目录，不纳入版本控制）
- `deploy/`：nginx 站点配置、systemd unit
- `docs/`：桥接契约与活动页接入指南

**既有代码改动**

- `frontend/` 现有官方模板页面（`views/HomeView.vue`、`AboutView.vue`、`components/HelloWorld.vue` 等）将被替换；`router/index.ts` 与 `main.ts` 需改写
- `frontend/vite.config.ts` 需增加开发代理（`/api`、`/content` → `:8000`）与 SDK 独立构建入口
- `frontend/src/assets/main.css` 现有绿色主题样式将被设计令牌替换

**新增依赖**

- 后端：`fastapi`、`uvicorn`、`sqlalchemy>=2`、`alembic`、`pydantic-settings`、`argon2-cffi`、`python-multipart`（`.venv` 当前仅有 `pip`）
- 前端：`axios`（当前未安装）

**部署与运维**

- nginx 需要新增站点配置：SPA history fallback、`/api` 与 `/content` 反代、`/data` 硬 404、`limit_req` 限流区、`client_max_body_size`
- uvicorn 需以 `--workers 1 --proxy-headers --forwarded-allow-ips` 启动（单 worker 是内存限流语义正确的前提，也与 SQLite 单写者模型一致）
- SMTP 为可选：未配置时使用 console 后端写出链接，注册与找回流程不依赖它

**不受影响**

- 绿地项目，无既有系统、数据或接口需要兼容；`D:\Ds_Projects\Documents\CEA\workspace\posters` 仅作为视觉参考，不被修改
