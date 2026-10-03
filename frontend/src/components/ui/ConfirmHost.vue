<script setup lang="ts">
/**
 * 确认对话框的宿主组件。
 *
 * **只在 `App.vue` 里挂一次**，与通知同理：确认框是应用级的，挂在视图里会在路由
 * 切换时连人带框一起消失，而调用方正等着一个永远不来的答案。
 *
 * ## 焦点始终落在"取消"那一侧
 *
 * `<dialog>` 的 `showModal()` 会把焦点给第一个可聚焦元素，而它同时也是**回车键
 * 的落点**。确认框存在的意义就是拦住一次误触，所以这里刻意让"取消"排在最前、
 * 拿到初始焦点 —— 一路回车下去的结果是"什么都没发生"，而不是"删掉了"。
 *
 * 也因此**不绑回车确认**：那正是确认框要防的那种事故。
 */
import { computed } from 'vue'

import Modal from '@/components/ui/Modal.vue'
import { useConfirm } from '@/composables/useConfirm'

const { request, settle } = useConfirm()

/**
 * 活动页发起的确认会多一行来源说明。
 *
 * 通知不加标记（见 `useToast` 与协议文档），这里加，差别在**这个框会拦住你、
 * 要求你按一下**：按钮文案由活动页给，一个全屏模态框配上"会话已过期，请确认"
 * 很容易被当成平台自己的话。浏览器的原生 `confirm` 同样会标出源，这里是同一个
 * 道理。措辞保持中性 —— 它是说明，不是警告。
 */
const fromEvent = computed(() => request.value?.fromEvent === true)
</script>

<template>
  <Modal
    class="confirm-modal"
    size="sm"
    :show-close="false"
    :open="request !== null"
    :title="request?.title ?? ''"
    @close="settle(false)"
  >
    <template v-if="request">
      <p class="confirm__message">{{ request.message }}</p>

      <p v-if="fromEvent" class="confirm__origin dim">来自活动页内容的请求</p>

      <div class="confirm__actions">
        <!--
          取消在前：它同时是初始焦点与回车落点。见文件头。
        -->
        <button
          class="btn btn--ghost"
          type="button"
          autofocus
          @click="settle(false)"
        >
          {{ request.cancelText }}
        </button>
        <button
          class="btn"
          :class="request.danger ? 'btn--danger' : 'btn--primary'"
          type="button"
          @click="settle(true)"
        >
          {{ request.confirmText }}
        </button>
      </div>
    </template>
  </Modal>
</template>

<style scoped>
.confirm__message {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.8;
  /* 活动页给的文案长度不受控，长串要能折行而不是撑破对话框 */
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.confirm__origin {
  margin: -6px 0 0;
  font-size: 11.5px;
}

.confirm__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
}
</style>
