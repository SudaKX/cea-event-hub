<script setup lang="ts">
/**
 * 登录页。
 *
 * 成功后回到 `?redirect=` 指向的原目标 —— 未登录访问管理台或个人中心时由路由守卫
 * 带上。**没有目标时进个人中心，而不是管理台**：管理台只对管理员开放，把它当默认值
 * 对多数登录者都是错的（普通成员一登录就被挡在门外，看起来像登录失败）。
 */
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const username = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)

async function onSubmit(): Promise<void> {
  error.value = ''
  busy.value = true
  try {
    await auth.signIn(username.value, password.value)
    const redirect = route.query.redirect
    /*
      原目标是**完整路径**（可能带 query），所以按字符串推；默认目标用命名路由，
      这样路径改了也不会在这里留下一个过期的字面量。
    */
    await router.push(
      typeof redirect === 'string' && redirect ? redirect : { name: 'profile' },
    )
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '登录失败，请稍后重试'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="auth">
    <form class="panel auth__card auth__card--closable" @submit.prevent="onSubmit">
      <!--
        关闭：回到首页。被守卫引到登录页的人（例如点进管理台）需要一个明确的"算了"，
        否则只能靠浏览器后退 —— 而那是浏览器的事，不是这一页的出口。
      -->
      <RouterLink class="auth__close" :to="{ name: 'home' }" aria-label="关闭并返回首页">
        ×
      </RouterLink>

      <h1 class="auth__title">
        CEA<span class="dim">/</span><em>登录</em><span class="cursor" aria-hidden="true" />
      </h1>

      <label class="field">
        <span class="field__label">用户名</span>
        <input v-model="username" name="username" autocomplete="username" required />
      </label>

      <label class="field">
        <span class="field__label">密码</span>
        <input
          v-model="password"
          type="password"
          name="password"
          autocomplete="current-password"
          required
        />
      </label>

      <p v-if="error" class="alert" role="alert">{{ error }}</p>

      <button class="btn btn--primary" type="submit" :disabled="busy">
        {{ busy ? '登录中…' : '登录' }}
      </button>

      <p class="mute auth__foot">
        <RouterLink to="/register">注册新账号</RouterLink>
        <span class="dim"> · </span>
        <RouterLink to="/reset">忘记密码</RouterLink>
      </p>
    </form>
  </main>
</template>
