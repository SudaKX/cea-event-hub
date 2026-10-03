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
 */
export const RESERVED_PREFIXES = [
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
