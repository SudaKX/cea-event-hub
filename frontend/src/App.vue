<script setup lang="ts">
/**
 * 应用外壳。
 *
 * `ToastHost` 与 `ConfirmHost` 都挂在这里，而**不是**任何视图里：两者都是应用级
 * 的浮层。挂在视图里的话，通知会随路由卸载而消失（正在看的成功提示凭空不见），
 * 确认框更糟 —— 它会连人带框一起消失，而等待答案的调用方永远等不到。
 */
import { RouterView } from 'vue-router'

import ConfirmHost from '@/components/ui/ConfirmHost.vue'
import ToastHost from '@/components/ui/ToastHost.vue'
</script>

<template>
  <RouterView />
  <ToastHost />
  <ConfirmHost />
</template>
