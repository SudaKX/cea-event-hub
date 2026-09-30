# Spec Delta

## Purpose

定义活动的生命周期、管理端维护方式，以及对外公开的活动目录与提交策略暴露，使平台能够承载多个互相独立的活动并统一控制其可提交性。

## ADDED Requirements

### Requirement: 活动标识与生命周期
每个活动 SHALL 由一个不可变、URL 安全的字符串标识（`event_id`）唯一确定。活动 SHALL 具有 `draft`、`live`、`archived` 三种状态，且仅 `live` 状态的活动对公开接口可见。

#### Scenario: 公开目录只列出已发布活动
- **WHEN** 匿名请求公开活动列表，库中存在 draft、live、archived 三种状态的活动
- **THEN** 响应只包含 live 状态的活动

#### Scenario: 活动标识不可变更
- **WHEN** 管理员尝试修改已创建活动的 `event_id`
- **THEN** 请求被拒绝，标识保持原值

#### Scenario: 不存在的活动返回 404
- **WHEN** 请求公开详情时传入未创建过的 `event_id`
- **THEN** 响应为 404

### Requirement: 活动级提交策略
每个活动 SHALL 声明提交策略：`submission_requires_login`（布尔，默认 false）、`submissions_open_at` 与 `submissions_close_at`（可空的 UTC 时间）以及可空的 `max_submissions`。未单独声明时，`submission_requires_login` MUST 取自全局种子配置。

#### Scenario: 未到开放时间时拒绝提交
- **WHEN** 当前时间早于 `submissions_open_at`，客户端向该活动提交信息
- **THEN** 提交被拒绝并返回 `403` 与错误码 `event_closed`

#### Scenario: 已过截止时间时拒绝提交
- **WHEN** 当前时间晚于 `submissions_close_at`，客户端向该活动提交信息
- **THEN** 提交被拒绝并返回 `403` 与错误码 `event_closed`

#### Scenario: 允许匿名提交的活动接受未登录请求
- **WHEN** `submission_requires_login` 为 false，未携带任何会话的客户端提交信息
- **THEN** 提交被正常受理

#### Scenario: 要求登录的活动拒绝未登录请求
- **WHEN** `submission_requires_login` 为 true，未携带会话的客户端提交信息
- **THEN** 提交被拒绝并返回 `401` 与错误码 `login_required`

### Requirement: 公开活动详情与配额可见性
公开活动详情 SHALL 返回该活动的标题、状态、内容版本号、提交策略字段，以及配额状态 `quota`，其中包含 `limit`、`used`、`remaining`。

#### Scenario: 匿名可读取已发布活动详情
- **WHEN** 匿名请求某个 live 活动的详情
- **THEN** 响应包含 `content_version`、提交策略字段与 `quota` 对象

#### Scenario: 不限额活动的 limit 为空
- **WHEN** 活动不设条数上限
- **THEN** `quota.limit` 为 null，`quota.remaining` 为 null

#### Scenario: 配额耗尽时剩余名额为零
- **WHEN** 活动的已用条数达到上限
- **THEN** `quota.remaining` 为 0

### Requirement: 每活动配额覆盖
活动 MAY 通过 `max_submissions` 覆盖默认条数上限。该值非空时 MUST 优先于按 `submission_requires_login` 推导出的默认值。

#### Scenario: 覆盖值优先于默认值
- **WHEN** 某可匿名提交的活动将 `max_submissions` 设为 100，而全局默认为 4096
- **THEN** 该活动的条数上限为 100

#### Scenario: 覆盖为空时回落到默认值
- **WHEN** 活动的 `max_submissions` 为空
- **THEN** 上限按 `submission_requires_login` 取对应的全局默认值

### Requirement: 管理端活动维护
活动的新建、修改、状态变更与删除 MUST 仅对 `admin` 角色开放。普通用户与匿名请求 MUST 被拒绝。

#### Scenario: 管理员创建活动
- **WHEN** admin 提交新的活动标识与标题
- **THEN** 活动被创建，初始状态为 `draft`

#### Scenario: 普通用户被拒绝
- **WHEN** 已登录的普通用户请求创建或修改活动
- **THEN** 响应为 `403`

#### Scenario: 匿名被拒绝
- **WHEN** 未携带会话的请求访问管理端活动接口
- **THEN** 响应为 `401`
