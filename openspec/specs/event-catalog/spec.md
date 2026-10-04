# event-catalog Specification

## Purpose

定义活动的生命周期、管理端维护方式，以及对外公开的活动目录与提交策略暴露，使平台能够承载多个互相独立的活动并统一控制其可提交性。

## Requirements

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

### Requirement: 活动可见性
每个活动 SHALL 具有可见性，取值为**整数码值** `0`（不公开）、`1`（公开）、`2`（公开并置顶），默认 `1`。码值是对外契约的一部分，改动必须配迁移。

- 码值 `0` 的活动 MUST NOT 出现在公开活动列表端点中
- 码值 `1` 与 `2` 的活动 SHALL 出现在公开活动列表端点中，其中 `2` MUST NOT 排在 `1` 之后
- 只有码值 `2` 的活动 SHALL 出现在首页的卡片区；码值 `1` 的活动通过列表端点与首页的标识补全可被找到

用一个有序刻度而不是"是否公开 + 是否置顶"两个布尔，是因为**置顶蕴含公开**：把一条不公开的活动置顶没有意义，两个布尔会允许这种无意义组合存在。

**可见性不是访问控制。** 码值 `0` 的活动 MUST 仍可按标识访问：它是"未公开"，不是"不存在"。把它做成 404 会同时伤掉两件事 —— 管理员无法把链接直接发给参与者，而参与者会以为自己拿到的链接是坏的。

可见性与状态正交：`status` 决定**能不能访问**（draft 一律 404），`visibility` 决定**在公开面露多少**。

公开响应 MUST NOT 包含可见性码值，但 SHALL 包含一个布尔 `pinned`（等价于码值为 2）：拿到链接的访客不需要知道这条是未公开的，而首页需要知道谁被置顶。管理端响应 SHALL 包含完整码值，否则设成不公开之后就再也找不回来。

#### Scenario: 不公开的活动不进公开列表
- **WHEN** 匿名请求公开活动列表，其中某活动为 live 且可见性为 0
- **THEN** 该活动不在列表中

#### Scenario: 不公开的活动仍可按标识访问
- **WHEN** 匿名以该活动的标识请求公开详情
- **THEN** 响应为 200 并返回该活动的信息

#### Scenario: 公开（1）的活动进列表但不进首页卡片区
- **WHEN** 某活动为 live 且可见性为 1
- **THEN** 它出现在公开活动列表端点与首页的标识补全中，但不进首页卡片区

#### Scenario: 置顶（2）的活动排在前面
- **WHEN** 同一活动的公开列表里既有可见性 1 也有可见性 2 的活动
- **THEN** 可见性 2 的排在前面，且它们的 `pinned` 为 true

#### Scenario: 既有活动默认为公开
- **WHEN** 升级到引入可见性的版本
- **THEN** 既有活动的可见性为 1，公开目录的内容与升级前一致

#### Scenario: 公开响应不泄露可见性码值
- **WHEN** 匿名读取某个可见性为 0 的活动的公开详情
- **THEN** 响应体不含可见性码值，`pinned` 为 false

#### Scenario: 非法可见性被拒绝
- **WHEN** 管理员提交 0/1/2 之外的取值
- **THEN** 请求被拒绝，`error.fields` 指出该字段

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
