/**
 * 匿名客户端标识。
 *
 * 由**宿主**产出并持久化，在 `hub:init` 中下发给活动页。iframe 处于不透明源，
 * 没有本地存储，所以这个值只能由宿主代持。
 *
 * **它不是凭据，因此可以放在 localStorage。** 被 XSS 读走无害；它只能用于
 * 分组与去重，不能用于鉴权 —— 直接后果是匿名用户没有"我的提交历史"。
 * 这也是它与会话 Cookie（HttpOnly、JS 读不到）可以处在完全不同安全等级的原因。
 */

const STORAGE_KEY = 'cea.clientId'

let cached: string | null = null

export function getClientId(): string {
  if (cached) return cached

  try {
    const existing = window.localStorage.getItem(STORAGE_KEY)
    if (existing) {
      cached = existing
      return existing
    }
  } catch {
    // 隐私模式下 localStorage 可能抛错：退化成"每个会话一个新标识"，
    // 功能不受影响，只是跨会话的分组能力变弱
  }

  const generated =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `c-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`

  cached = generated
  try {
    window.localStorage.setItem(STORAGE_KEY, generated)
  } catch {
    /* 忽略：见上 */
  }
  return generated
}

/** 仅供测试使用。 */
export function resetClientIdCache(): void {
  cached = null
}
