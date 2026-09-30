# Spec Delta

## Purpose

托管每个活动的网页内容并提供安全的投放途径，使各活动能够保留自行编写 HTML 的自由度，同时由平台统一承担存储、版本与访问控制。

## ADDED Requirements

### Requirement: 内容目录托管
平台 SHALL 以 `/content/{event_id}/{path}` 的形式提供活动静态内容。`index.html` 的响应 MUST 禁止缓存，带内容版本或内容哈希的静态资源 MAY 长期缓存。

#### Scenario: 请求活动入口页
- **WHEN** 请求 `/content/{event_id}/index.html`
- **THEN** 返回该文件内容且响应头包含禁止缓存的指令

#### Scenario: 静态资源可长期缓存
- **WHEN** 请求活动目录下的图片或样式资源
- **THEN** 响应头包含长期缓存指令

#### Scenario: 未知路径返回 404
- **WHEN** 请求活动目录下不存在的文件
- **THEN** 响应为 404

#### Scenario: 目录遍历被阻止
- **WHEN** 请求路径中包含尝试逃逸出活动目录的片段
- **THEN** 响应为 404 且不返回活动目录之外的文件

### Requirement: 内容版本与缓存失效
每次活动内容被重新投放时，平台 SHALL 递增该活动的 `content_version`。活动详情接口 MUST 返回当前版本号，以供宿主在承载时附加为查询参数。

#### Scenario: 重新投放内容后版本递增
- **WHEN** 管理员为某活动上传新的内容包并成功解压
- **THEN** 该活动的 `content_version` 增加

#### Scenario: 版本号可在公开详情读取
- **WHEN** 请求该活动的公开详情
- **THEN** 响应中的 `content_version` 为递增后的值

### Requirement: 内容包的安全解压
内容包（zip）上传与解压时，平台 MUST 对条目名做规范化校验，且 MUST 拒绝任何规范化后位于目标活动目录之外的条目。平台 MUST 拒绝符号链接条目。检测到违规时 MUST 中止投放且 MUST NOT 部分落盘。

#### Scenario: 含目录逃逸的条目被拒绝
- **WHEN** 上传的内容包中存在名称包含 `../` 的条目
- **THEN** 整次投放被拒绝，活动内容目录不被修改

#### Scenario: 绝对路径条目被拒绝
- **WHEN** 上传的内容包中存在以路径分隔符或盘符开头的条目
- **THEN** 整次投放被拒绝

#### Scenario: 符号链接条目被拒绝
- **WHEN** 上传的内容包中存在符号链接条目
- **THEN** 整次投放被拒绝

#### Scenario: 条目数量或解压体积超限被拒绝
- **WHEN** 内容包的条目数或解压后总体积超过配置上限
- **THEN** 整次投放被拒绝

### Requirement: 内容与接口的 CORS 不对称策略
`/content/**` 的响应 MUST 携带 `Access-Control-Allow-Origin: *`。`/api/**` 的响应 MUST NOT 携带任何 CORS 响应头。

#### Scenario: 沙箱活动页可读取自身目录的数据文件
- **WHEN** 处于不透明源的沙箱 iframe 请求自身活动目录下的 JSON 数据文件
- **THEN** 请求因通配 CORS 头而成功

#### Scenario: 沙箱活动页无法直接调用后端接口
- **WHEN** 处于不透明源的沙箱 iframe 尝试直接请求 `/api/v1/**`
- **THEN** 请求因缺少 CORS 响应头而被浏览器阻断

### Requirement: 内容投放清单
管理员 SHALL 能够列出某活动当前已投放的文件及其体积。

#### Scenario: 列出已投放文件
- **WHEN** admin 请求某活动的内容清单
- **THEN** 响应列出该活动目录下的文件与各自体积

#### Scenario: 非管理员被拒绝
- **WHEN** 普通用户请求内容清单
- **THEN** 响应为 `403`
