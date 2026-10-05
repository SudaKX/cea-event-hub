<script setup lang="ts">
/**
 * 个人中心：当前登录者的落脚点。
 *
 * **它为什么存在。** 在此之前，登录成功一律跳管理台，而管理台只对管理员开放 ——
 * 普通成员登录后立刻被挡在门外，看起来像登录失败；而登出按钮只存在于管理台外壳里，
 * 于是非管理员登录之后**没有出口**。这一页把这两件事一次解决：它是登录后的默认落脚
 * 点，也是任何已登录用户都能结束自己会话的地方。
 *
 * **只读。** 本期不提供改密码、改资料、邮箱验证等操作（见 proposal 的"本期不做"），
 * 因此这里没有表单、没有写操作。
 *
 * **只显示身份，不显示凭据。** 会话凭据是 HttpOnly Cookie，前端读不到，这一页也就
 * 不可能泄露它 —— 别为了"让用户看到登录状态"而把凭据搬到可读的地方（design.md
 * 决策 7）。
 */
import { useAuthStore } from '@/stores/auth'
import { useSignOut } from '@/composables/useSignOut'

const auth = useAuthStore()
const { signOut } = useSignOut()

const ROLE_LABEL: Record<string, string> = {
  admin: '管理员',
  user: '普通成员',
}
</script>

<template>
  <main class="auth">
    <div class="panel auth__card profile">
      <h1 class="auth__title">CEA<span class="dim">/</span><em>个人中心</em></h1>

      <!-- 走到这里必然已登录（路由守卫保证），user 为空只可能是会话刚失效 -->
      <template v-if="auth.user">
        <dl class="profile__meta">
          <div class="profile__pair">
            <dt>显示名</dt>
            <dd>{{ auth.user.display_name }}</dd>
          </div>
          <div class="profile__pair">
            <dt>用户名</dt>
            <dd class="mono">{{ auth.user.username }}</dd>
          </div>
          <div class="profile__pair">
            <dt>身份</dt>
            <dd>
              <span class="tag" :class="auth.isAdmin ? 'tag--live' : ''">
                {{ ROLE_LABEL[auth.user.role] ?? auth.user.role }}
              </span>
            </dd>
          </div>
          <div class="profile__pair">
            <dt>邮箱</dt>
            <dd class="mono">
              {{ auth.user.email ?? '未绑定' }}
              <span v-if="auth.user.email && !auth.user.email_verified" class="tag">
                未验证
              </span>
            </dd>
          </div>
        </dl>

        <p class="dim profile__note">
          邮箱与密码的修改、邮箱验证等功能尚未提供；本期这里只用来查看身份与退出登录。
        </p>

        <div class="profile__actions">
          <RouterLink class="btn btn--ghost" :to="{ name: 'home' }">返回首页</RouterLink>
          <button class="btn btn--danger" type="button" @click="signOut">
            退出登录
          </button>
        </div>
      </template>

      <p v-else class="mute" role="status">正在读取登录状态…</p>
    </div>
  </main>
</template>

<style scoped>
.profile {
  /* 比登录卡片宽一点：这里要横排四项身份，窄了会频繁折行 */
  max-width: 420px;
}

.profile__meta {
  margin: 0;
  display: grid;
  gap: 10px;
  padding: 0 0 14px;
  border-bottom: 1px solid var(--line);
}

.profile__pair {
  display: flex;
  gap: 10px;
  align-items: baseline;
  font-size: 13px;
}

.profile__pair dt {
  flex: none;
  width: 56px;
  color: var(--mute);
  font-size: 12px;
}

.profile__pair dd {
  margin: 0;
  min-width: 0;
  overflow-wrap: anywhere;
}

.profile__note {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.8;
}

.profile__actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
}
</style>
