# 权限矩阵

管理端接口的完整权限清单。**整层挂 `require_admin`**（router 级依赖），而不是
逐个端点自己加守卫——后者迟早会漏掉一个。

## 三层身份

| 身份 | 判定 |
|---|---|
| 匿名 | 无有效会话 |
| `user` | 已登录且 `role = user` |
| `admin` | 已登录且 `role = admin` |

未登录一律 `401` `login_required`（先证明你是谁）；已登录但权限不足一律 `403`
`forbidden`（你不够格）。两者刻意区分。

## 活动

| 操作 | 端点 | 匿名 | user | admin |
|---|---|---|---|---|
| 公开活动列表 | `GET /events` | o | o | o |
| 公开活动详情 | `GET /events/{id}` | o | o | o |
| 活动列表（含未发布） | `GET /admin/events` | x | x | o |
| 活动详情（含未发布） | `GET /admin/events/{id}` | x | x | o |
| 创建 / 修改 / 删除活动 | `POST|PATCH|DELETE /admin/events/{id}` | x | x | o |

公开接口只暴露 `live` 活动；对 `draft` 返回 **404 而不是 403**，后者会泄露
"这个标识存在但还没上线"。

## 内容

| 操作 | 端点 | 匿名 | user | admin |
|---|---|---|---|---|
| 读取活动网页内容 | `GET /content/{id}/**` | o | o | o |
| 内容清单 | `GET /admin/events/{id}/content` | x | x | o |
| 投放内容包 | `POST /admin/events/{id}/content` | x | x | o |

活动内容是**公开只读**的。因此"能读到别的活动的内容"不是漏洞，而是设计——
也正因如此，活动页里不能放任何秘密。

## 提交

| 操作 | 端点 | 匿名 | user | admin |
|---|---|---|---|---|
| 提交信息 | `POST /events/{id}/submissions[:files]` | o\* | o | o |
| 我的提交历史 | `GET /me/submissions` | **x** | o | o |
| 下载自己的附件 | `GET /submissions/{sid}/files/{fid}` | x | o | o |
| 下载任意附件 | 同上 | x | x | o |
| 提交列表与筛选 | `GET /admin/events/{id}/submissions` | x | x | o |
| 审核（变更状态） | `PATCH /admin/submissions/{id}` | x | x | o |
| 批量审核 | `POST /admin/submissions:review` | x | x | o |
| 删除提交 | `DELETE /admin/submissions/{id}` | x | x | o |
| 批量删除 | `POST /admin/submissions:delete` | x | x | o |

\* 仅当活动的 `submission_requires_login = false`。要求登录的活动对匿名返回
`401` `login_required`。

**匿名用户没有提交历史**：匿名标识不是凭据，靠它认人等于"猜到一个 id 就能读别人
的提交记录"。这是去掉凭据属性后必然的代价。

**他人附件返回 404 而不是 403**：403 会确认"这个附件存在但不给你看"。

## 用户

| 操作 | 端点 | 匿名 | user | admin |
|---|---|---|---|---|
| 注册（提交与核销两步） | `POST /auth/register[/verify]` | o | o | o |
| 登录 / 登出 | `POST /auth/login|logout` | o | o | o |
| 当前身份 | `GET /auth/me` | x | o | o |
| 修改自己的密码 | `POST /auth/password` | x | o | o |
| 邮箱验证 | `POST /auth/verify-email[/request]` | x | o | o |
| 自助密码找回 | `POST /auth/forgot-password` | o | o | o |
| 凭令牌重置密码 | `POST /auth/reset` | o | o | o |
| 用户列表与筛选 | `GET /admin/users` | x | x | o |
| 修改角色 / 状态 / 显示名 | `PATCH /admin/users/{id}` | x | x | o |
| 批量改角色 / 状态 | `POST /admin/users:bulk` | x | x | o |
| 签发重置令牌 | `POST /admin/users/{id}/reset-token` | x | x | o |
| **删除账号** | `DELETE /admin/users/{id}` | x | x | o |
| 申请自己的邀请码 | `POST /invitations` | x | o | o |
| 查看自己发出的邀请码 | `GET /invitations` | x | o | o |
| 删除自己未使用的邀请码 | `DELETE /invitations/{id}` | x | o | o |
| 查看全部邀请码 | `GET /admin/invitations` | x | x | o |
| 创建邀请码 | `POST /admin/invitations` | x | x | o |
| 使邀请码失效 | `POST /admin/invitations/{id}/revoke` | x | x | o |
| 读写平台开关 | `GET｜PUT /admin/switches` | x | x | o |

注意"注册 / 登录 / 找回 / 重置"对匿名开放是**刻意的**——它们正是获得身份的入口。

**邀请码的权限分两层。** 普通用户只能碰**自己名下**的码（申请、删除未使用的、查看使用
情况），这是"个人分享"；管理员能看全部、能创建、能让任意一张失效，这是"运营动作"。
两者的字段相同，区别只在范围 —— 所以用户端与管理端共用同一份响应形状。

两个**平台开关**只对管理员开放，因为它们影响所有人：`invitations_paused` 拒绝一切
邀请码校验（含此前发出的），`invitation_issuance_paused` 只挡新申请。它们存在库里，
因此改完**立即生效**，不需要重启进程 —— 这是应急手段，等一次重启就失去意义。

**删除与停用回答的是不同的问题**，因此是并列的两个动作，不设"必须先停用"的门槛：

| | 停用 | 删除 |
|---|---|---|
| 回答 | "这个人不在了" | "这个账号本就不该存在" |
| 可逆 | 是，随时恢复 | 否 |
| 他提交的东西 | 留着，署名完好 | **同样留着**，但署名此后只剩编号 |
| 何时用 | 成员退出、暂时封禁 | 误注册、垃圾账号 |

删除**不会**带走他的提交 —— 那是社团收集的数据，与账号生命周期是两件事（与"停用不删历史提交"同一条取向）。`events.owner_id`、`submissions.user_id`、`submissions.reviewed_by` 会被置空：他创建的活动与做过的审核都还在，只是不再归属于任何人。提交列表会把这类署名显示成`u:{id}` 加「已删除」标记。

## 两条额外约束

**不能移除最后一个管理员。** 降级或停用最后一个启用的管理员会返回
`409` `last_admin_protected`。这条与首次启动引导合起来，保证系统不会经由界面
进入零管理员状态。

**不能停用自己。** 避免管理员把自己锁在门外。

**不能删除自己。** 同理，返回 `400`。

删除时那条"最后管理员"守卫仍然在服务层，但**通过接口走不到它**：能通过管理员校验的操作者本身就是一个可用管理员，因此"除目标之外还有几个可用管理员"永远至少是 1；要删掉最后一个可用管理员，唯一可能是删自己，而那已经先被"不能删除自己"拦下了。它在服务层仍然有效（例如操作者的账号已停用而目标账号是仅剩的可用管理员），由 `tests/test_user_deletion.py` 里的服务层用例守着。这与下面批量操作里"停用最后一个管理员"的处境相同。

### 批量操作里它们要整批一起算

`POST /admin/users:bulk` 一次可以改一批人的角色与／或启用状态。上面两条约束在批量
下**不能逐个检查**：

- 逐个调用单条逻辑时，两个管理员一起降级会双双通过 —— 第一次检查看到"还有另一个
  管理员在"，第二次检查时第一次的改动还没落库、同样通过，**结果一个管理员都不剩**。
- 所以批量实现改成先算出"改完之后还剩几个**可用管理员**"（角色为 admin 且已启用，
  与 `count_admins` 口径一致），不足一个就整批拒绝。
- 停用自己的检查同理：名单里只要含操作者本人就整批拒绝，而不是"改了一半才发现
  自己在名单里"。

## 角色与状态变更会吊销会话

| 变更 | 副作用 |
|---|---|
| 提权 / 降权 | 该用户全部会话失效 |
| 停用 | 该用户全部会话失效，且后续登录被拒 |
| 口令修改 | 除发起改密的会话外，全部失效 |
| 凭令牌重置口令 | 全部失效 |

不吊销的话，降级后的用户在旧会话里仍然持有管理权限，而"停用"会退化成
"下次登录才生效"。

## 测试对照

| 契约 | 测试 |
|---|---|
| 活动权限 | `test_events.py::TestAdminAuthorization` |
| 内容权限 | `test_content.py::TestContentListing` |
| 提交与附件权限 | `test_attachments.py::TestAttachmentDownload` |
| 审核与删除 | `test_review.py::TestReview`、`TestDeletion` |
| 用户管理 | `test_review.py::TestUserManagement` |
| 最后管理员保护 | `test_cannot_remove_the_last_admin` |
| 角色变更吊销会话 | `test_demotion_revokes_sessions` |
