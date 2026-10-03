/**
 * 认证状态。
 *
 * **不持有任何令牌。** 会话在 HttpOnly Cookie 里，JS 读不到也无需读 —— 这既是
 * XSS 防护，也是"活动页零凭证"能够成立的前提：宿主没有可以交出去的东西。
 *
 * 启动时用 `GET /auth/me` 恢复身份；Cookie 存在就自动登录，不存在就是匿名。
 */

import { defineStore } from 'pinia'
import { computed, readonly, ref } from 'vue'

import * as authApi from '@/api/auth'
import { setUnauthorizedHandler } from '@/api/client'
import type { User } from '@/types/api'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  /** 是否已完成启动时的会话恢复尝试 */
  const ready = ref(false)

  const isLoggedIn = computed(() => user.value !== null)
  const isAdmin = computed(() => user.value?.role === 'admin')

  /** 登录态变化的订阅者（桥接层据此向活动页下发更新） */
  const listeners = new Set<(user: User | null) => void>()

  function onChange(listener: (user: User | null) => void): () => void {
    listeners.add(listener)
    return () => listeners.delete(listener)
  }

  function notify(): void {
    for (const listener of listeners) listener(user.value)
  }

  function setUser(next: User | null): void {
    user.value = next
    notify()
  }

  function clear(): void {
    if (user.value !== null) setUser(null)
  }

  /** 启动时恢复会话。Cookie 有效则拿到用户，否则保持匿名。 */
  async function restore(): Promise<void> {
    try {
      user.value = await authApi.me()
    } catch {
      // 401 是正常情况（未登录），不是错误
      user.value = null
    } finally {
      ready.value = true
    }
  }

  async function signIn(username: string, password: string): Promise<User> {
    const loggedIn = await authApi.login({ username, password })
    setUser(loggedIn)
    return loggedIn
  }

  async function signUp(
    payload: authApi.RegisterPayload,
  ): Promise<authApi.RegistrationStarted> {
    /*
      注册刻意不自动登录 —— 而且现在**也登录不了**：这一步只建立待验证占位，
      账号要到邮件链接被打开才创建。返回的只有"是否重入"。
    */
    return authApi.register(payload)
  }

  async function signOut(): Promise<void> {
    try {
      await authApi.logout()
    } catch {
      // 登出是尽力而为：服务端可能已经删了会话，或者网络就是不通。
      // 无论哪种情况，本地状态都必须清掉，否则界面会停在一个已失效的身份上。
    } finally {
      setUser(null)
    }
  }

  // 任何请求收到 401 都意味着会话已失效（过期、被吊销、账号被停用），
  // 此时必须立刻回到未登录态，而不是继续显示旧身份
  setUnauthorizedHandler(() => clear())

  return {
    // 只读暴露：直接赋值会绕过 onChange 通知，桥接层就收不到登录态变化了
    user: readonly(user),
    ready: readonly(ready),
    isLoggedIn,
    isAdmin,
    clear,
    setUser,
    onChange,
    restore,
    signIn,
    signOut,
    signUp,
  }
})
