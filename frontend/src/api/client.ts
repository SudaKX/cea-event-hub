/**
 * axios 实例：统一基地址、错误解包与 401 处理。
 *
 * **不携带任何令牌**：会话走 HttpOnly Cookie，由浏览器自动附加。这既让 JS 读不到
 * 凭据（XSS 偷不走），也意味着桥接层不需要把令牌交给活动页 —— 活动页零凭证这条
 * 约束正是由此成立（design.md 决策 4）。
 */

import axios, { AxiosError, type AxiosInstance } from 'axios'

/** 后端统一错误信封的形状 */
export interface ApiErrorBody {
  error: {
    code: string
    message: string
    fields?: Record<string, string>
  }
}

/** 前端各处统一处理的错误对象 */
export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly fields?: Record<string, string>

  constructor(code: string, message: string, status: number, fields?: Record<string, string>) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.fields = fields
  }
}

export const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'
export const CONTENT_BASE = import.meta.env.VITE_CONTENT_BASE ?? '/content'

/** 401 时的回调。由 auth store 注册，避免 api 层反向依赖 store。 */
type UnauthorizedHandler = () => void
let onUnauthorized: UnauthorizedHandler | null = null

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  onUnauthorized = handler
}

/** 仅供测试：手动触发 401 处理，验证"会话失效即清态"。 */
export function triggerUnauthorized(): void {
  onUnauthorized?.()
}

/**
 * 把 axios 错误统一成 ApiError。
 *
 * 抽成纯函数是为了能直接单测：错误形状的映射不该需要起一个 HTTP 服务才能验证。
 * 后端保证所有错误都走统一信封；拿不到信封时（网络错误、超时、反向代理返回的
 * 错误页）给一个等价的兜底，调用方只需处理一种形状。
 */
export function toApiError(error: AxiosError<ApiErrorBody>): ApiError {
  const status = error.response?.status ?? 0
  const body = error.response?.data

  const code = body?.error?.code ?? (status === 0 ? 'network_error' : 'unknown_error')
  const message = body?.error?.message ?? error.message ?? '请求失败'
  return new ApiError(code, message, status, body?.error?.fields)
}

export function createClient(): AxiosInstance {
  const instance = axios.create({
    baseURL: API_BASE,
    // 会话凭据在 HttpOnly Cookie 里，必须让浏览器带上
    withCredentials: true,
    timeout: 30_000,
    headers: { Accept: 'application/json' },
  })

  instance.interceptors.response.use(
    (response) => response,
    (error: AxiosError<ApiErrorBody>) => {
      const mapped = toApiError(error)

      if (mapped.status === 401) {
        // 会话失效（过期、被吊销、账号被停用）时统一清态，
        // 并由桥接层向活动页下发未登录的描述符
        onUnauthorized?.()
      }

      return Promise.reject(mapped)
    },
  )

  return instance
}

export const http = createClient()
