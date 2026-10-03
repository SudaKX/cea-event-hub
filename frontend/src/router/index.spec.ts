/**
 * 路由：保留前缀与静态路径的优先级。
 *
 * 两件事各自都会**静默**出错，所以都值得钉住：
 *
 * 1. 保留前缀清单漏一项 → 那一页会被当成活动标识（顶层路径一律解释为活动）
 * 2. 平台自己的静态路径必须赢过 `/:eventId` 这条通配式活动路由
 *
 * 第 2 点**与声明顺序无关**：vue-router 4 按路径具体度打分（静态段高于动态段）。
 * 这一点是实测确认的 —— 这里原本写着"必须排在 `/:eventId` 之前"，把路由表顺序
 * 对调之后测试依然全绿，那句话是 vue-router 3 的行为。
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import { isReservedPath, RESERVED_PREFIXES, router } from './index'

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('保留前缀', () => {
  it('平台自身的顶层路径都被登记', () => {
    for (const prefix of [
      'admin',
      'login',
      'register',
      'reset',
      'verify-email',
      'verify-registration',
      'api',
      'content',
      'data',
      'sdk',
      'assets',
    ]) {
      expect(RESERVED_PREFIXES).toContain(prefix)
      expect(isReservedPath(`/${prefix}`)).toBe(true)
      expect(isReservedPath(`/${prefix}/deeper`)).toBe(true)
    }
  })

  it('活动标识不会被误判为保留路径', () => {
    expect(isReservedPath('/2026spring')).toBe(false)
    expect(isReservedPath('/verify-registrations')).toBe(false)
    expect(isReservedPath('/')).toBe(false)
  })
})

describe('静态路径赢过活动路由', () => {
  it('核销页匹配到自己的路由，而不是被当成活动标识', async () => {
    /*
      顶层路径一律解释为活动标识。静态段的具体度高于动态段，因此核销页赢 ——
      否则邮件里的链接会打开一个"活动不存在"的页面，而路由表看上去完全正常。
    */
    await router.push('/verify-registration?token=abc')
    expect(String(router.currentRoute.value.name)).toBe('verify-registration')
  })

  it('邮箱验证页同理', async () => {
    await router.push('/verify-email?token=abc')
    expect(String(router.currentRoute.value.name)).toBe('verify-email')
  })

  it('单个路径段仍然被解释为活动标识', async () => {
    // 这是"其余顶层路径都是活动"那条规则本身
    await router.push('/2026spring')
    expect(String(router.currentRoute.value.name)).toBe('event')
    expect(router.currentRoute.value.params.eventId).toBe('2026spring')
  })
})
