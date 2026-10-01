<script setup lang="ts">
/** 邮箱验证落地页。用户从邮件里的链接带 `?token=` 进来。 */
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { verifyEmail } from '@/api/auth'
import { ApiError } from '@/api/client'

const route = useRoute()
const state = ref<'pending' | 'done' | 'failed'>('pending')
const message = ref('')

const token = computed(() => {
  const value = route.query.token
  return typeof value === 'string' ? value : ''
})

onMounted(async () => {
  if (!token.value) {
    state.value = 'failed'
    message.value = '链接缺少令牌参数'
    return
  }
  try {
    await verifyEmail(token.value)
    state.value = 'done'
  } catch (caught) {
    state.value = 'failed'
    message.value = caught instanceof ApiError ? caught.message : '验证失败'
  }
})
</script>

<template>
  <main class="auth">
    <div class="panel auth__card">
      <h1 class="auth__title">CEA<span class="dim">/</span><em>邮箱验证</em></h1>

      <p v-if="state === 'pending'" class="mute">正在验证…</p>
      <p v-else-if="state === 'done'" class="ok">邮箱已验证。</p>
      <p v-else class="alert" role="alert">{{ message }}</p>

      <p class="mute auth__foot"><RouterLink to="/admin">进入管理台</RouterLink></p>
    </div>
  </main>
</template>
