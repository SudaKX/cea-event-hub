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

      <!--
        出口是**首页**而不是管理台：这一页可能在任何浏览器里被打开（邮件客户端
        点进来），因此不能假设对方已登录、更不能假设他是管理员。
      -->
      <p class="mute auth__foot"><RouterLink to="/">返回首页</RouterLink></p>
    </div>
  </main>
</template>
