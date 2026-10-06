/**
 * 路由与保留前缀。
 *
 * 保留前缀之外的顶层路径一律被解释为**活动标识** —— 这是需求里"对于别的路径，
 * SPA 路由导航到活动界面"的落点。通配因此必须排在最后，且必须显式排除保留
 * 前缀，否则 `/login` 会被当成一个叫 "login" 的活动。
 *
 * 视图按用途分目录：`admin/`（管理台）、`auth/`（登录注册等）、`event/`（活动页
 * 宿主）。不属于任何一组的单页（404）放在 `views/` 根下。
 */

import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

/**
 * 这些顶层路径属于平台自身，永远不会被当作活动标识。
 *
 * `/api`、`/content`、`/data`、`/sdk`、`/assets` 实际上由 nginx 或后端处理、
 * 根本到不了前端路由，但仍然列在这里：一是让这份清单成为完整的"平台保留字"，
 * 二是防止开发环境下 Vite 代理配置变化时它们意外落到活动路由上。
 *
 * `/draft` 与 `/develop` 同理，它们都**只在开发模式下有意义**：
 *
 * - `/draft`：草稿活动内容，由后端在开发模式下挂载（见 docs/dev-harness.md）。
 *   前端路由不注册它 —— 它是服务端路径，和后端的 `/content` 是同一类东西。
 * - `/develop`：开发调试台。它**是**一条前端路由，但只在开发构建里注册
 *   （见下方 routes 的说明）。
 *
 * 清单是构建期常量、**不随构建模式变化**：让它在生产里少两项，会使"哪些标识不能
 * 用作活动"在不同构建下不一致 —— 同一个标识在开发环境是活动、在生产环境是别的。
 * 代价是活动标识不能再叫 `draft` 或 `develop`，与 `content`、`data`、`sdk` 已付出
 * 的代价同类。
 */
export const RESERVED_PREFIXES = [
  'admin',
  'login',
  'register',
  'reset',
  'verify-email',
  'verify-registration',
  'profile',
  'api',
  'content',
  'data',
  'sdk',
  'assets',
  'draft',
  'develop',
] as const

export function isReservedPath(path: string): boolean {
  const first = path.split('/').filter(Boolean)[0]
  return first !== undefined && (RESERVED_PREFIXES as readonly string[]).includes(first)
}

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'home',
    component: () => import('@/views/HomeView.vue'),
    meta: { public: true },
  },
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/auth/LoginView.vue'),
    meta: { public: true },
  },
  {
    path: '/register',
    name: 'register',
    component: () => import('@/views/auth/RegisterView.vue'),
    meta: { public: true },
  },
  {
    path: '/reset',
    name: 'reset',
    component: () => import('@/views/auth/ResetView.vue'),
    meta: { public: true },
  },
  {
    path: '/verify-email',
    name: 'verify-email',
    component: () => import('@/views/auth/VerifyEmailView.vue'),
    meta: { public: true },
  },
  {
    /*
      核销页。它必须同时出现在两个地方，各自负责一件事：

      - **路由表里**：让这个路径能被匹配到。`RESERVED_PREFIXES` 只管"不要把它当作
        活动"这条判断，它本身不注册任何路由。
      - **`RESERVED_PREFIXES` 里**：让它在通配兜底与活动路由的语义里被排除。

      注意**顺序无关紧要**：vue-router 4 按路径具体度打分（静态段高于动态段），
      所以 `/verify-registration` 无论声明在哪里都赢过 `/:eventId`。这里曾写着
      "必须排在它之前" —— 那是 vue-router 3 的行为，实测不成立。
    */
    path: '/verify-registration',
    name: 'verify-registration',
    component: () => import('@/views/auth/VerifyRegistrationView.vue'),
    meta: { public: true },
  },
  {
    path: '/profile',
    name: 'profile',
    component: () => import('@/views/profile/ProfileView.vue'),
    /*
      需要登录。未登录访问时由守卫带上 `?redirect=/profile` 引到登录页，登录后回到
      这里 —— 与访问管理台完全同一条路径，不引入新机制。

      注意它同时出现在上面的 `RESERVED_PREFIXES` 里：只加路由的话，`/profile` 会被
      当成标识为 profile 的活动、由活动页逻辑接管，**且不报任何错**。
    */
    meta: { requiresAuth: true },
  },
  {
    path: '/admin',
    component: () => import('@/components/layout/AdminShell.vue'),
    meta: { requiresAuth: true },
    children: [
      {
        path: '',
        redirect: { name: 'admin-events' },
      },
      {
        path: 'events',
        name: 'admin-events',
        component: () => import('@/views/admin/EventsView.vue'),
      },
      {
        path: 'events/:eventId',
        name: 'admin-event-detail',
        component: () => import('@/views/admin/EventDetailView.vue'),
        props: true,
      },
      {
        path: 'submissions',
        name: 'admin-submissions',
        component: () => import('@/views/admin/SubmissionsView.vue'),
      },
      {
        path: 'invitations',
        name: 'admin-invitations',
        component: () => import('@/views/admin/InvitationsView.vue'),
      },
      {
        path: 'users',
        name: 'admin-users',
        component: () => import('@/views/admin/UsersView.vue'),
        meta: { requiresAdmin: true },
      },
    ],
  },
  {
    // 活动页：保留前缀之外的顶层路径
    path: '/:eventId',
    name: 'event',
    component: () => import('@/views/event/EventView.vue'),
    props: true,
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFoundView.vue'),
    meta: { public: true },
  },
]

/*
  开发调试台：**只在开发构建里注册**（见 docs/dev-harness.md）。

  两条约束，缺一条这个守卫就形同虚设：

  1. 条件必须是**构建期常量**。生产构建里它被替换成 `false`，整个分支连同下面那句
     动态 `import` 一起被删除，于是调试台既不注册路由、也不产出 chunk。
  2. 本文件里**不能出现对该组件的静态引入**（哪怕只是为了取一个类型）。静态引入会
     把组件拉进产物，而守卫不会有任何报错 —— 产物里就是多了一个块而已。

  这条性质不能靠单元测试钉住：测试环境里开发判据为真、生产构建里为假，也就是说
  **测试看到的路由表和生产的不是同一张**。验收方式是构建后在产物里检索只属于调试台
  的标记串，见 change 的 tasks 5.1 / 5.2。
*/
if (import.meta.env.DEV) {
  routes.push({
    path: '/develop',
    name: 'develop',
    component: () => import('@/views/develop/DevelopView.vue'),
    meta: { public: true },
  })
}

export const router = createRouter({
  // history 模式：服务端已配置 history fallback，深层路径可直接打开
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()

  // 首次导航前先尝试恢复会话，否则刷新页面会被误判成未登录
  if (!auth.ready) await auth.restore()

  if (to.meta.requiresAuth && !auth.isLoggedIn) {
    // 记住原目标，登录后回到这里
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  if (to.meta.requiresAdmin && !auth.isAdmin) {
    // 非管理员不显示用户管理入口，直接访问也拒绝
    return { name: 'admin-events' }
  }

  return true
})

export default router
