<script setup lang="ts">
/**
 * 应用外壳。
 *
 * `ToastHost` 挂在这里而**不是**任何视图里：通知是应用级的，挂在视图里会随路由
 * 卸载而消失（正在看的成功提示会凭空不见），而且每个视图都得重复一遍定位与层级。
 */
import { RouterView } from 'vue-router'

import ToastHost from '@/components/ui/ToastHost.vue'
</script>

<template>
  <RouterView />
  <ToastHost />
</template>
