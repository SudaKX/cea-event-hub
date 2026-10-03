<script setup lang="ts">
/**
 * 注册核销落地页。用户从邮件里的链接带 `?token=` 进来。
 *
 * **进入即自动提交** —— 链接本身就是那个动作，没有需要用户再确认的东西。所以这里
 * 不需要输入框，也不需要按钮。
 *
 * 失败一律指向同一个出路：回注册页用同样的用户名与邮箱重新提交。链接过期、已被
 * 使用过、或者期间被人抢注了用户名，用户能做的事都一样。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { verifyRegistration } from '@/api/auth'
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
    await verifyRegistration(token.value)
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
      <h1 class="auth__title">CEA<span class="dim">/</span><em>完成注册</em></h1>

      <p v-if="state === 'pending'" class="mute">正在验证…</p>

      <template v-else-if="state === 'done'">
        <p class="ok">账号已创建，现在可以登录了。</p>
        <RouterLink class="btn btn--primary" to="/login">去登录</RouterLink>
      </template>

      <template v-else>
        <p class="alert" role="alert">{{ message }}</p>
        <p class="mute auth__lead">
          链接 <strong>10 分钟内</strong>有效且只能使用一次。用同样的用户名与邮箱
          重新提交即可收到新链接。
        </p>
        <RouterLink class="btn btn--ghost" to="/register">回注册页重新提交</RouterLink>
      </template>
    </div>
  </main>
</template>
