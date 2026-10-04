<script setup lang="ts">
/**
 * 登录页。
 *
 * 成功后回到 `?redirect=` 指向的原目标 —— 未登录访问管理台时由路由守卫带上。
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
    await router.push(typeof redirect === 'string' && redirect ? redirect : '/admin')
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '登录失败，请稍后重试'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="auth">
    <form class="panel auth__card" @submit.prevent="onSubmit">
      <h1 class="auth__title">
        CEA<span class="dim">/</span><em>登录</em><span class="cursor" aria-hidden="true" />
      </h1>
      <p class="mute auth__lead">社团活动平台管理入口。</p>

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
