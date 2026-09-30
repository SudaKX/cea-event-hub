<script setup lang="ts">
/**
 * 注册页。
 *
 * 注册**不自动登录**："注册"与"获得会话"是两件明确的事。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()

const username = ref('')
const password = ref('')
const email = ref('')
const inviteCode = ref('')
const error = ref('')
const fieldErrors = ref<Record<string, string>>({})
const done = ref(false)
const busy = ref(false)

const canSubmit = computed(
  () => username.value.trim().length > 0 && password.value.length > 0,
)

async function onSubmit(): Promise<void> {
  error.value = ''
  fieldErrors.value = {}
  busy.value = true
  try {
    await auth.signUp({
      username: username.value.trim(),
      password: password.value,
      email: email.value.trim() || undefined,
      invite_code: inviteCode.value.trim() || undefined,
    })
    done.value = true
    setTimeout(() => void router.push('/login'), 1200)
  } catch (caught) {
    if (caught instanceof ApiError) {
      error.value = caught.message
      // 字段级错误直接标到对应输入框上
      fieldErrors.value = caught.fields ?? {}
    } else {
      error.value = '注册失败，请稍后重试'
    }
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="auth">
    <form class="panel auth__card" @submit.prevent="onSubmit">
      <h1 class="auth__title">CEA<span class="dim">/</span><em>注册</em></h1>
      <p class="mute auth__lead">任何人都可以注册，管理员可后续提升权限。</p>

      <label class="field">
        <span class="field__label">用户名</span>
        <input v-model="username" autocomplete="username" required />
        <span v-if="fieldErrors.username" class="field__error">{{ fieldErrors.username }}</span>
      </label>

      <label class="field">
        <span class="field__label">口令</span>
        <input v-model="password" type="password" autocomplete="new-password" required />
        <span class="field__hint dim">至少 8 个字符。长度比复杂度更有效。</span>
        <span v-if="fieldErrors.password" class="field__error">{{ fieldErrors.password }}</span>
      </label>

      <label class="field">
        <span class="field__label">邮箱<span class="dim">（可选）</span></span>
        <input v-model="email" type="email" autocomplete="email" />
        <span v-if="fieldErrors.email" class="field__error">{{ fieldErrors.email }}</span>
      </label>

      <label class="field">
        <span class="field__label">邀请码<span class="dim">（若已启用）</span></span>
        <input v-model="inviteCode" autocomplete="off" />
        <span v-if="fieldErrors.invite_code" class="field__error">
          {{ fieldErrors.invite_code }}
        </span>
      </label>

      <p v-if="error" class="alert" role="alert">{{ error }}</p>
      <p v-if="done" class="ok">注册成功，正在跳转到登录…</p>

      <button class="btn btn--primary" type="submit" :disabled="busy || !canSubmit">
        {{ busy ? '提交中…' : '注册' }}
      </button>

      <p class="mute auth__foot">已有账号？<RouterLink to="/login">去登录</RouterLink></p>
    </form>
  </main>
</template>
