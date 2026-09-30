<script setup lang="ts">
/**
 * 管理台外壳。
 *
 * 三个入口：活动、提交、用户。用户入口只对管理员可见 —— 非管理员既看不到它，
 * 直接访问也会被路由守卫挡回。
 */
import { computed } from 'vue'
import { RouterLink, RouterView, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()

const navItems = computed(() => {
  const items = [
    { name: 'admin-events', label: '活动', hint: '创建、发布与投放内容' },
    { name: 'admin-submissions', label: '提交', hint: '审核与清理提交' },
  ]
  if (auth.isAdmin) {
    items.push({ name: 'admin-users', label: '用户', hint: '角色与状态管理' })
  }
  return items
})

async function onSignOut(): Promise<void> {
  await auth.signOut()
  await router.push('/login')
}
</script>

<template>
  <div class="shell">
    <aside class="shell__side">
      <div class="shell__brand">
        <span class="shell__brand-mark">CEA</span>
        <span class="dim">/</span>
        <span class="shell__brand-sub">活动平台</span>
      </div>

      <nav class="shell__nav">
        <RouterLink
          v-for="item in navItems"
          :key="item.name"
          class="shell__link"
          :to="{ name: item.name }"
        >
          <span class="shell__link-label">{{ item.label }}</span>
          <span class="shell__link-hint dim">{{ item.hint }}</span>
        </RouterLink>
      </nav>

      <div class="shell__foot">
        <div class="shell__who">
          <span class="mono">{{ auth.user?.display_name ?? '—' }}</span>
          <span class="tag">{{ auth.user?.role ?? '匿名' }}</span>
        </div>
        <button class="btn btn--ghost btn--small" type="button" @click="onSignOut">
          退出登录
        </button>
      </div>
    </aside>

    <main class="shell__main">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: 232px 1fr;
  min-height: 100%;
}

.shell__side {
  display: flex;
  flex-direction: column;
  gap: 22px;
  padding: 22px 18px;
  border-right: 1px solid var(--line);
  background: var(--panel);
}

.shell__brand {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font: 700 17px/1 var(--mono);
  letter-spacing: 0.06em;
}

.shell__brand-mark {
  color: var(--red-hi);
}

.shell__brand-sub {
  font-size: 12px;
  color: var(--mute);
  letter-spacing: 0.1em;
}

.shell__nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
}

.shell__link {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 9px 11px;
  border-radius: var(--radius-control);
  color: var(--bone);
  text-decoration: none;
  transition: background var(--transition-fast);
}

.shell__link:hover {
  background: rgba(255, 255, 255, 0.03);
  text-decoration: none;
}

/* 当前项用左侧红条标记，而不是整块高亮 —— 更接近海报的克制感 */
.shell__link.router-link-active {
  background: var(--red-soft);
  box-shadow: inset 2px 0 0 var(--red);
}

.shell__link-label {
  font: 600 14px/1.4 var(--sans);
}

.shell__link-hint {
  font-size: 11px;
}

.shell__foot {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 14px;
  border-top: 1px solid var(--line);
}

.shell__who {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.shell__main {
  min-width: 0;
  padding: 26px 28px 48px;
}

@media (max-width: 720px) {
  .shell {
    grid-template-columns: 1fr;
  }

  .shell__side {
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }
}
</style>
