/**
 * 任务 12.3（续）：会话失效时 auth store 必须立刻回到未登录态。
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import { setUnauthorizedHandler, triggerUnauthorized } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import type { User } from '@/types/api'

const USER: User = {
  id: 7,
  username: 'alice',
  display_name: 'Alice',
  role: 'user',
  email: null,
  email_verified: false,
}

describe('auth store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    setUnauthorizedHandler(null)
  })

  it('初始为匿名且未就绪', () => {
    const auth = useAuthStore()
    expect(auth.isLoggedIn).toBe(false)
    expect(auth.isAdmin).toBe(false)
    expect(auth.ready).toBe(false)
  })

  it('管理员身份可识别', () => {
    const auth = useAuthStore()
    auth.setUser({ ...USER, role: 'admin' })
    expect(auth.isAdmin).toBe(true)
  })

  it('401 后立即清空用户', () => {
    const auth = useAuthStore()
    auth.setUser(USER)
    expect(auth.isLoggedIn).toBe(true)

    // store 在创建时注册了 401 处理器
    triggerUnauthorized()

    expect(auth.user).toBeNull()
    expect(auth.isLoggedIn).toBe(false)
  })

  it('登录态变化会通知订阅者', () => {
    const auth = useAuthStore()
    const seen: Array<User | null> = []
    const unsubscribe = auth.onChange((user) => seen.push(user))

    auth.setUser(USER)
    auth.clear()

    expect(seen).toEqual([USER, null])
    unsubscribe()
  })

  it('清除时用户本来就是空则不重复通知', () => {
    const auth = useAuthStore()
    const seen: Array<User | null> = []
    const unsubscribe = auth.onChange((user) => seen.push(user))

    auth.clear()

    expect(seen).toEqual([])
    unsubscribe()
  })

  it('登出即使请求失败也要清本地状态', async () => {
    const auth = useAuthStore()
    auth.setUser(USER)
    // 测试环境里没有后端，logout 请求必然失败；本地状态仍必须清掉，
    // 而且不该把异常抛给调用方（否则界面会卡在"登出中"）
    await expect(auth.signOut()).resolves.toBeUndefined()
    expect(auth.user).toBeNull()
  })
})
