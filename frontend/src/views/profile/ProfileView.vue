<script setup lang="ts">
/**
 * 个人中心：当前登录者的落脚点。
 *
 * **它为什么存在。** 在此之前，登录成功一律跳管理台，而管理台只对管理员开放 ——
 * 普通成员登录后立刻被挡在门外，看起来像登录失败；而登出按钮只存在于管理台外壳里，
 * 于是非管理员登录之后**没有出口**。这一页把这两件事一次解决：它是登录后的默认落脚
 * 点，也是任何已登录用户都能结束自己会话的地方。
 *
 * **版式：头部 + 流式卡片。** 早先这里是两张居中的窄卡片，像两个互不相干的浮层；
 * 现在改成"一张横跨的头部（我是谁）+ 若干张高度不等、按两列流动的卡片（我能做什么）"。
 * 卡片各自独立成块，因此加一张不必重排其余部分 —— 这是这一版式真正的价值。
 *
 * **只读。** 本期不提供改密码、改资料、邮箱验证等操作（见 proposal 的"本期不做"），
 * 因此这里没有表单、没有写操作。
 *
 * **只显示身份，不显示凭据。** 会话凭据是 HttpOnly Cookie，前端读不到，这一页也就
 * 不可能泄露它 —— 别为了"让用户看到登录状态"而把凭据搬到可读的地方（design.md
 * 决策 7）。
 */
import { computed } from 'vue'

import { useAuthStore } from '@/stores/auth'
import { useSignOut } from '@/composables/useSignOut'
import InvitationCard from './InvitationCard.vue'

const auth = useAuthStore()
const { signOut } = useSignOut()

const ROLE_LABEL: Record<string, string> = {
  admin: '管理员',
  user: '普通成员',
}

/** 头像位：没有头像上传，就用显示名的首字 —— 比放一个通用人像更有辨识度 */
const initial = computed(() => (auth.user?.display_name ?? '?').trim().slice(0, 1))
</script>

<template>
  <main class="me">
    <div class="me__inner">
      <template v-if="auth.user">
        <!--
          头部：横跨两列。左是"我是谁"，右是这一页唯一的出口动作。
        -->
        <header class="panel me__hero">
          <span class="me__avatar" aria-hidden="true">{{ initial }}</span>
          <div class="me__who">
            <h1 class="me__name">{{ auth.user.display_name }}</h1>
            <p class="me__handle mono">{{ auth.user.username }}</p>
            <p class="me__tags">
              <span class="tag" :class="auth.isAdmin ? 'tag--live' : ''">
                {{ ROLE_LABEL[auth.user.role] ?? auth.user.role }}
              </span>
              <span v-if="auth.user.email && !auth.user.email_verified" class="tag">
                邮箱未验证
              </span>
              <span v-else-if="!auth.user.email" class="tag">未绑定邮箱</span>
            </p>
          </div>
          <RouterLink class="btn btn--ghost me__back" :to="{ name: 'home' }">
            返回首页
          </RouterLink>
        </header>

        <!--
          流式卡片区。`columns` 让每张卡按自己的高度排下去，而不是被拉成等高的格子 ——
          这正是参照里那种"卡片各自成块"的观感。
        -->
        <div class="me__flow">
          <section class="panel me__card">
            <h2 class="panel__title">账号</h2>
            <p class="panel__lead">你的账号信息。</p>
            <ul class="panel__rows">
              <li class="panel__row">
                <span class="panel__row-label">用户名</span>
                <span class="mono">{{ auth.user.username }}</span>
              </li>
              <li class="panel__row">
                <span class="panel__row-label">身份</span>
                <span>{{ ROLE_LABEL[auth.user.role] ?? auth.user.role }}</span>
              </li>
              <li class="panel__row">
                <span class="panel__row-label">邮箱</span>
                <span class="mono me__value">
                  {{ auth.user.email ?? '未绑定' }}
                  <span v-if="auth.user.email && !auth.user.email_verified" class="tag">
                    未验证
                  </span>
                </span>
              </li>
            </ul>
          </section>

          <InvitationCard class="panel me__card" />

          <section class="panel me__card">
            <h2 class="panel__title">会话</h2>
            <p class="panel__lead">
              退出之后<strong>这台设备需要重新登录</strong>。换一台电脑、或把设备借给别人
              之前，请从这里退出。
            </p>
            <div class="me__actions">
              <button class="btn btn--danger" type="button" @click="signOut">
                退出登录
              </button>
            </div>
          </section>

          <section class="panel me__card">
            <h2 class="panel__title">目前还没有</h2>
            <p class="panel__lead">
              修改密码、绑定邮箱、修改显示名、删除账号、查看自己的提交记录 ——
              这些都还没有。<strong>忘记密码</strong>可以先试「忘记密码」，走不通时
              请管理员签发一个重置令牌。
            </p>
          </section>
        </div>
      </template>

      <p v-else class="mute" role="status">正在读取登录状态…</p>
    </div>
  </main>
</template>

<style scoped>
/*
  页面外壳：**可滚动的内容页**，不是居中的一张卡。
  `min-height: 100%` 撑满，宽度上限保证大屏上不会拉成一行读不下来的长条。
*/
.me {
  min-height: 100%;
  padding: 34px 24px 48px;
  display: flex;
  justify-content: center;
}

.me__inner {
  width: 100%;
  max-width: 880px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* 头部：左头像、中间身份、右边动作 */
.me__hero {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 18px 20px;
}

.me__avatar {
  flex: none;
  width: 52px;
  height: 52px;
  display: grid;
  place-items: center;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-surface);
  background: var(--select-soft);
  color: var(--bone);
  font: 700 20px/1 var(--mono);
}

.me__who {
  flex: 1;
  min-width: 0;
}

.me__name {
  font-size: clamp(18px, 2.4vw, 22px);
  overflow-wrap: anywhere;
}

.me__handle {
  margin: 4px 0 0;
  font-size: 12.5px;
  color: var(--mute);
  overflow-wrap: anywhere;
}

.me__tags {
  margin: 8px 0 0;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.me__back {
  flex: none;
}

/*
  流式两列。`break-inside: avoid` 是关键：没有它，卡片会被从中间劈开分到两列。
  窄屏落成一列（与首页、管理台的断点取向一致）。
*/
.me__flow {
  columns: 2;
  column-gap: 16px;
}

.me__card {
  break-inside: avoid;
  /* `columns` 布局里用 margin 而不是 gap —— gap 对多列无效 */
  margin: 0 0 16px;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.me__value {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  overflow-wrap: anywhere;
}

.me__actions {
  display: flex;
  justify-content: flex-end;
}

@media (max-width: 720px) {
  .me__flow {
    columns: 1;
  }

  .me__hero {
    flex-wrap: wrap;
  }
}
</style>
