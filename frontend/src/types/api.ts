/** 与后端 DTO 对应的前端类型。字段名与后端保持一致，避免两层各自映射。 */

export interface User {
  id: number
  username: string
  display_name: string
  role: 'user' | 'admin'
  email: string | null
  email_verified: boolean
}

export interface UserAdmin extends User {
  is_active: boolean
  created_at: string
}

export interface QuotaState {
  /** null 表示不限额 */
  limit: number | null
  used: number
  /** 不限额时为 null */
  remaining: number | null
}

export interface EventPublic {
  id: string
  title: string
  summary: string | null
  status: 'draft' | 'live' | 'archived'
  content_version: number
  entry_path: string
  submission_requires_login: boolean
  submissions_open_at: string | null
  submissions_close_at: string | null
  quota: QuotaState
  /**
   * 是否被置顶（可见性码值为 2）。首页凭它决定进不进卡片区。
   *
   * 公开响应给的是**布尔**而不是码值：码值 0 意味着"不公开"，而拿到链接的访客
   * 不需要知道这条是未公开的。
   */
  pinned: boolean
}

export interface EventAdmin extends EventPublic {
  /**
   * 在公开面露多少。码值是**对外契约**，取值见 `EVENT_VISIBILITY`。
   *
   * 与 `status` 正交：`status` 管**能不能访问**（draft 一律 404），`visibility`
   * 管**在公开面露多少**。
   *
   * 只在管理端响应里出现完整码值：设成不可见之后就靠它找回来。
   */
  visibility: number
  max_submissions: number | null
  /**
   * 单个提交者最多几份；`null` 表示不限。
   *
   * **对匿名提交，这条限制防不住故意绕过**：匿名提交者的标识是客户端自报的
   * `client_id`，换一个浏览器就是新的提交者。要真正限制，得让活动要求登录。
   */
  max_per_submitter: number | null
  owner_id: number | null
  created_at: string
  updated_at: string
}

/**
 * 审核状态码。**码值是对外契约**（导出与筛选按它走），与后端
 * `core/enums.py` 的 `SubmissionStatus` 一一对应，改一处必须改两处。
 *
 * 取值与含义见 `@/domain/submission`。
 */
export type SubmissionStatus = number

export interface SubmissionFile {
  id: number
  original_name: string
  size_bytes: number
  mime: string | null
  sha256: string
  created_at: string
}

export interface Submission {
  id: number
  event_id: string
  kind: string
  status: SubmissionStatus
  submitter: string
  from_authenticated_user: boolean
  payload: Record<string, unknown>
  created_at: string
  files: SubmissionFile[]
}

export interface SubmissionCreated {
  submission: Submission
  /** true 表示这是此前那次提交，不是新建 —— 提示文案应当不同 */
  deduplicated: boolean
}

export interface ContentFile {
  path: string
  size_bytes: number
}

export interface ContentList {
  event_id: string
  content_version: number
  entry_path: string
  files: ContentFile[]
}

export interface ContentDeployResult {
  event_id: string
  file_count: number
  total_bytes: number
  content_version: number
}

export interface ResetToken {
  user_id: number
  username: string
  /** 明文只出现这一次 */
  token: string
  expires_at: string
}
