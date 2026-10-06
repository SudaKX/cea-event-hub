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
import { beforeEach, describe, expect, it, vi } from 'vitest'

const me = vi.hoisted(() => vi.fn())
vi.mock('@/api/auth', () => ({
  me: (...args: unknown[]) => me(...args),
}))

import { isReservedPath, RESERVED_PREFIXES, router } from './index'
import { useAuthStore } from '@/stores/auth'
import type { User } from '@/types/api'

const ALICE: User = {
  id: 7,
  username: 'alice',
  display_name: '张爱丽',
  role: 'user',
  email: 'alice@example.com',
  email_verified: true,
}

beforeEach(() => {
  vi.clearAllMocks()
  // 默认未登录：会话恢复失败是最常见的情形
  me.mockRejectedValue(new Error('401'))
  setActivePinia(createPinia())
})

describe('保留前缀', () => {
  it('路由表里的每个静态顶层路径都登记在保留前缀清单里', () => {
    /*
      **清单从路由表推导，而不是手写一份。** 这里原先手写了 11 个前缀，于是它只能
      断言"我列的这些都登记了" —— 规格要求的是**两侧一致**，所以其中一侧的清单必须
      来自另一侧；两边各写一份的话，往路由表加一页却忘了登记，测试照样绿，而那一页
      会被活动页逻辑接管且不报任何错。

      动态段（`/:eventId`、`/:pathMatch(.*)*`）跳过：它们的首段不是平台的静态路径。
    */
    const staticPrefixes = new Set(
      router
        .getRoutes()
        .map((route) => route.path)
        .filter((path) => path !== '/' && !path.includes(':'))
        .map((path) => path.split('/').filter(Boolean)[0]),
    )

    expect(staticPrefixes.size).toBeGreaterThan(0)
    expect(staticPrefixes).toContain('profile')

    for (const prefix of staticPrefixes) {
      expect(RESERVED_PREFIXES, `${prefix} 没登记进保留前缀`).toContain(prefix)
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

describe('开发专用的两个前缀', () => {
  /*
    这两条把"清单与路由表两侧一致"那条要求扩展到开发专用的路径上。

    `develop` 是**一条只在开发构建里注册的路由**，而保留前缀清单是构建期常量、
    不随构建模式变化（理由见 index.ts）。因此在测试环境（开发判据为真）里，
    它必须同时出现在两处 —— 而上面那条"路由表里的每个静态顶层路径都登记在清单里"
    已经会强制这一点，这里再显式钉一次，是为了让失败信息直接指向原因。
  */
  it('develop 同时出现在路由表与保留前缀清单里', () => {
    const paths = router.getRoutes().map((route) => route.path)

    expect(paths, '调试台路由没有注册').toContain('/develop')
    expect(RESERVED_PREFIXES, 'develop 没登记进保留前缀').toContain('develop')
  })

  it('draft 只登记在保留前缀清单里（它由服务端处理，前端不注册路由）', () => {
    const paths = router.getRoutes().map((route) => route.path)

    expect(RESERVED_PREFIXES).toContain('draft')
    expect(paths).not.toContain('/draft')
  })

  it('调试台路径不会被当成活动标识', async () => {
    await router.push('/develop?src=/draft/demo/index.html')

    expect(String(router.currentRoute.value.name)).toBe('develop')
    expect(router.currentRoute.value.params.eventId).toBeUndefined()
  })
})

describe('静态路径赢过活动路由', () => {  it('核销页匹配到自己的路由，而不是被当成活动标识', async () => {
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

describe('需要登录的页面', () => {
  it('未登录访问 /profile 被引到登录页，并带上回跳目标', async () => {
    /*
      个人中心复用管理台那条既有路径，不引入新机制：守卫带上 `?redirect=`，登录页
      读它并跳回去。这里断言的是守卫这一侧 —— 恢复失败即未登录，正是最常见的情形。
    */
    await router.push('/profile')

    expect(String(router.currentRoute.value.name)).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/profile')
  })

  it('已登录时 /profile 直接进得去', async () => {
    me.mockResolvedValue(ALICE)

    await router.push('/profile')
    // 守卫会先恢复会话；恢复成功即已登录，于是不再被弹走
    expect(useAuthStore().isLoggedIn).toBe(true)
    expect(String(router.currentRoute.value.name)).toBe('profile')
  })

  it('/profile 不会被当成活动标识', async () => {
    // 与核销页同理：它是平台的静态路径，必须赢过 `/:eventId`
    me.mockResolvedValue(ALICE)

    await router.push('/profile')

    expect(router.currentRoute.value.params.eventId).toBeUndefined()
  })
})
