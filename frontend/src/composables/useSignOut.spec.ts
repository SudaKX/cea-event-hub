/**
 * 登出动作，以及"它只实现一次"这件事。
 *
 * 两半都要有：
 *
 * 1. **行为**：请求服务端结束会话，然后回登录页 —— 顺序不能反。
 * 2. **结构性**：没有任何视图或组件自己写一遍登出。只测行为是不够的 —— 把某一处改回
 *    "自己 `auth.signOut()` 再跳转"，行为完全一样，行为断言照样绿。上一个变更正是
 *    栽在这个形状上：同一份渲染在列表与详情各写一遍，补标记时只改了一处。
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

const logout = vi.hoisted(() => vi.fn())
vi.mock('@/api/auth', () => ({
  logout: (...args: unknown[]) => logout(...args),
  me: vi.fn().mockRejectedValue(new Error('401')),
}))

import { useSignOut } from './useSignOut'
import { useAuthStore } from '@/stores/auth'

const SRC = join(__dirname, '..')

function sourceFiles(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) {
      sourceFiles(path, found)
    } else if (/\.(vue|ts)$/.test(entry) && !entry.endsWith('.spec.ts')) {
      found.push(path)
    }
  }
  return found
}

/**
 * 去掉注释再扫描。
 *
 * **第一版没去，于是它命中了注释里的示例代码** —— 那些注释恰好是在说明"不要自己写
 * 一遍登出"。静态断言扫的是文本，不是语法树，所以必须先把注释摘掉，否则一句说明
 * 就能把它变成假红。
 */
function stripComments(source: string): string {
  return source
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1')
}

beforeEach(() => {
  vi.clearAllMocks()
  setActivePinia(createPinia())
  logout.mockResolvedValue(undefined)
})

function makeRouter() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/login', name: 'login', component: { template: '<div />' } },
      { path: '/profile', name: 'profile', component: { template: '<div />' } },
    ],
  })
  return router
}

/**
 * 最小宿主：`useSignOut()` 里的 `useRouter()` 靠**注入**拿路由，所以必须在组件里调用。
 * 直接在测试体里调它会拿到 undefined。
 */
function host(router: ReturnType<typeof makeRouter>) {
  return defineComponent({
    setup() {
      const { signOut } = useSignOut()
      // setup 返回的就是渲染函数，挂载即触发一次登出
      return async () => {
        await signOut()
        return null
      }
    },
  })
}

/**
 * 挂载宿主。
 *
 * **pinia 必须只有一份。** 先 `setActivePinia(pinia)` 设状态、再让 `mount` 用**同一个**
 * `pinia` —— 否则测试设的是 A 实例、组件里 `useAuthStore()` 拿到的是 B，断言看的
 * 是一个从没登录过的 store（表现为"钩子里永远是 false"这种莫名其妙的红）。
 */
function mountHost(router: ReturnType<typeof makeRouter>, withUser = false) {
  const pinia = createPinia()
  setActivePinia(pinia)
  if (withUser) {
    useAuthStore().setUser({
      id: 1,
      username: 'a',
      display_name: 'A',
      role: 'user',
      email: null,
      email_verified: true,
    })
  }
  return {
    auth: useAuthStore(),
    wrapper: mount(host(router), { global: { plugins: [pinia, router] } }),
  }
}

describe('登出动作', () => {
  it('请求服务端结束会话，然后回登录页', async () => {
    const router = makeRouter()
    await router.push('/profile')
    await router.isReady()

    mountHost(router)

    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('login'))
    expect(logout).toHaveBeenCalledTimes(1)
  })

  it('先结束会话再导航，不留下"已离开页面但本地仍认为已登录"的一瞬', async () => {
    /*
      顺序反过来的话，目标页若需要登录就会把用户弹回来 —— 表现为"点了登出又被弹回
      原页面"，而日志里什么都看不出来。
    */
    const router = makeRouter()
    await router.push('/profile')
    await router.isReady()

    /*
      挂钩子放在挂载**之后**：`mountHost` 里的登出在挂载时就跑，钩子若在那之前注册，
      它会把"带着登录态进入 /profile"那一次也算进来。而初始 push 已经在上面完成，
      所以它不会被记录。
    */
    const { auth, wrapper } = mountHost(router, true)
    const seen: boolean[] = []
    router.afterEach(() => seen.push(auth.isLoggedIn))

    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('login'))
    expect(seen.length).toBeGreaterThan(0)
    expect(seen.every((loggedIn) => loggedIn === false)).toBe(true)
    wrapper.unmount()
  })
})

describe('登出只实现一次', () => {
  it('除了 composable 自己，没有别处直接调用 auth.signOut', () => {
    /*
      **结构性断言**：它守的不是行为，而是"实现只有一份"。行为断言抓不到这个 ——
      两处各自实现时行为完全一致，只有当其中一处需要修改（比如登出后不再回登录页）
      时，漏改另一处才会显形。
    */
    const offenders = sourceFiles(SRC)
      .filter((path) => !path.endsWith(join('composables', 'useSignOut.ts')))
      .filter((path) => /\.signOut\s*\(/.test(stripComments(readFileSync(path, 'utf8'))))
      .map((path) => path.replace(SRC, 'src'))

    expect(offenders, `这些文件自己实现了一遍登出：${offenders.join(', ')}`).toEqual([])
  })

  it('管理台与个人中心都用这个 composable', () => {
    for (const path of [
      join(SRC, 'components', 'layout', 'AdminShell.vue'),
      join(SRC, 'views', 'profile', 'ProfileView.vue'),
    ]) {
      const source = readFileSync(path, 'utf8')
      expect(source, `${path} 没用 useSignOut`).toContain('useSignOut')
    }
  })
})
