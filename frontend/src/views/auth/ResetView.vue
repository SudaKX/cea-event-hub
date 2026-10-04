<script setup lang="ts">
/**
 * 密码重置页。
 *
 * 两条签发路径共用这一个页面：
 * - 邮件可用：用户从邮件链接带 `?token=` 进来
 * - 邮件不可用：管理员签发令牌，线下转交后同样带 `?token=` 进来
 *
 * 没有 token 时展示"发起找回"表单（仅在配置开启自助找回时有效）。
 */
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { forgotPassword, resetPassword } from '@/api/auth'
import { ApiError } from '@/api/client'

const route = useRoute()
const router = useRouter()

const token = computed(() => {
  const value = route.query.token
  return typeof value === 'string' ? value : ''
})

const email = ref('')
const newPassword = ref('')
const error = ref('')
const fieldErrors = ref<Record<string, string>>({})
const sent = ref(false)
const done = ref(false)
const busy = ref(false)

async function onRequest(): Promise<void> {
  error.value = ''
  busy.value = true
  try {
    await forgotPassword(email.value.trim())
    // 无论邮箱是否存在都显示同样的结果 —— 否则就成了账号枚举接口
    sent.value = true
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '请求失败'
  } finally {
    busy.value = false
  }
}

async function onReset(): Promise<void> {
  error.value = ''
  fieldErrors.value = {}
  busy.value = true
  try {
    await resetPassword(token.value, newPassword.value)
    done.value = true
    setTimeout(() => void router.push('/login'), 1400)
  } catch (caught) {
    if (caught instanceof ApiError) {
      error.value = caught.message
      fieldErrors.value = caught.fields ?? {}
    } else {
      error.value = '重置失败'
    }
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="auth">
    <form v-if="token" class="panel auth__card" @submit.prevent="onReset">
      <h1 class="auth__title">CEA<span class="dim">/</span><em>重置密码</em></h1>
      <p class="mute auth__lead">设置一个新密码。链接只能使用一次。</p>

      <label class="field">
        <span class="field__label">新密码</span>
        <input v-model="newPassword" type="password" autocomplete="new-password" required />
        <span v-if="fieldErrors.new_password" class="field__error">
          {{ fieldErrors.new_password }}
        </span>
      </label>

      <p v-if="error" class="alert" role="alert">{{ error }}</p>
      <p v-if="done" class="ok">密码已更新，正在跳转到登录…</p>

      <button class="btn btn--primary" type="submit" :disabled="busy">
        {{ busy ? '提交中…' : '设置新密码' }}
      </button>
      <p class="mute auth__foot"><RouterLink to="/login">返回登录</RouterLink></p>
    </form>

    <form v-else class="panel auth__card" @submit.prevent="onRequest">
      <h1 class="auth__title">CEA<span class="dim">/</span><em>找回密码</em></h1>
      <p class="mute auth__lead">
        输入绑定过的邮箱，我们会发送一封含重置链接的邮件。若平台未启用邮件，
        请联系管理员签发一次性令牌。
      </p>

      <label class="field">
        <span class="field__label">邮箱</span>
        <input v-model="email" type="email" autocomplete="email" required />
      </label>

      <p v-if="error" class="alert" role="alert">{{ error }}</p>
      <p v-if="sent" class="ok">如果该邮箱已注册，重置邮件已经发出。</p>

      <button class="btn btn--primary" type="submit" :disabled="busy">
        {{ busy ? '提交中…' : '发送重置链接' }}
      </button>
      <p class="mute auth__foot"><RouterLink to="/login">返回登录</RouterLink></p>
    </form>
  </main>
</template>
