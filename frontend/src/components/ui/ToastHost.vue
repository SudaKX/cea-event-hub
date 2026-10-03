<script setup lang="ts">
/**
 * 通知栈的宿主组件。
 *
 * **只在 `App.vue` 里挂一次**，任何视图都不该自己渲染它 —— 通知是应用级的，
 * 挂在视图里会随路由卸载而消失（正在看的成功提示会凭空不见），而且每个视图都要
 * 重复一遍定位与层级。
 *
 * 位置固定在右下角：左侧是管理台的导航，右上是内容区的开头，右下是唯一既不挡
 * 导航也不挡标题的角落。
 */
import { useToast, type ToastTone } from '@/composables/useToast'

const { toasts, dismiss, hold, resume } = useToast()

/**
 * 无障碍角色按语气分：
 *
 * - 失败用 `alert`（assertive）：读屏会打断当前朗读
 * - 其余用 `status`（polite）：等用户停下来再念
 *
 * 全都用 `alert` 会很吵；全都用 `status` 又会让失败被淹没。
 */
function roleFor(tone: ToastTone): 'alert' | 'status' {
  return tone === 'error' ? 'alert' : 'status'
}
</script>

<template>
  <div class="toasts" aria-label="通知">
    <TransitionGroup name="toast">
      <div
        v-for="toast in toasts"
        :key="toast.id"
        class="toast"
        :class="`toast--${toast.tone}`"
        :role="roleFor(toast.tone)"
        @pointerenter="hold(toast.id)"
        @pointerleave="resume(toast.id)"
      >
        <!--
          图标纯粹是装饰：含义由文本承载，所以对读屏隐藏。
          它的作用是**让人一眼看出这是成功还是失败** —— 只靠左侧一条色条，
          在余光里几乎看不见。
        -->
        <span class="toast__icon" aria-hidden="true">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2">
            <template v-if="toast.tone === 'ok'">
              <path d="M3.5 8.5l3 3 6-7" stroke-linecap="round" stroke-linejoin="round" />
            </template>
            <template v-else-if="toast.tone === 'error'">
              <path d="M8 4v5" stroke-linecap="round" />
              <path d="M8 12h.01" stroke-linecap="round" />
            </template>
            <template v-else>
              <path d="M8 7.5V12" stroke-linecap="round" />
              <path d="M8 4h.01" stroke-linecap="round" />
            </template>
          </svg>
        </span>

        <span class="toast__text">{{ toast.message }}</span>
        <button
          class="toast__close"
          type="button"
          aria-label="关闭通知"
          @click="dismiss(toast.id)"
        >
          ×
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toasts {
  position: fixed;
  right: 20px;
  bottom: 20px;
  /* 通知只是路过，不该拦住底下真正可点的东西 */
  z-index: 60;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  /* 容器本身不吃指针事件，否则右下角一整片区域都会变成死区 */
  pointer-events: none;
  max-width: min(420px, calc(100vw - 40px));
}

.toast {
  pointer-events: auto;
  display: flex;
  /* 图标与文本按首行对齐：文本可能折行，图标不该跟着跑到中间去 */
  align-items: flex-start;
  gap: 12px;
  width: 100%;
  /*
    比常规面板松一些：通知是**一闪而过**的东西，拥挤的排版在余光里根本认不出来。
    字号刻意不动 —— 加大的是留白与图标，不是文字。
  */
  padding: 15px 16px;
  min-height: 62px;
  background: var(--panel);
  border: 1px solid var(--line-strong);
  border-left-width: 4px;
  border-radius: var(--radius-surface);
  box-shadow: 0 16px 36px rgba(0, 0, 0, 0.6);
  font-size: 13px;
  line-height: 1.6;
}

/*
  语气由**图标 + 左侧色条**共同承载。
  只靠色条不够：那是 4px 宽的一条边，在余光里几乎看不见；只靠文字颜色又对色觉
  障碍不友好。圆形底衬让图标本身也有形状。
*/
.toast__icon {
  flex: none;
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border-radius: 50%;
}

.toast__icon svg {
  width: 16px;
  height: 16px;
}

.toast--ok {
  border-left-color: var(--red);
}

.toast--ok .toast__icon {
  background: var(--red-soft);
  color: var(--red-hi);
}

.toast--error {
  border-left-color: var(--red-hi);
  /* 失败比成功多一层淡淡的底衬，让它在整屏里先被看见 */
  background: var(--red-soft);
}

.toast--error .toast__icon {
  background: var(--red-hi);
  color: var(--panel);
}

.toast--info {
  border-left-color: var(--dim);
}

.toast--info .toast__icon {
  background: var(--select-soft);
  color: var(--mute);
}

.toast--error .toast__text {
  color: var(--red-hi);
}

.toast__text {
  flex: 1;
  min-width: 0;
  /* 图标高 28px、行高约 21px，补一点上边距让首行与图标视觉居中 */
  padding-top: 3px;
  /* 长消息（比如批量结果）要能折行，而不是撑破容器 */
  overflow-wrap: anywhere;
}

.toast__close {
  flex: none;
  width: 24px;
  height: 24px;
  padding: 0;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--dim);
  font-size: 15px;
  line-height: 1;
  cursor: pointer;
  transition: color var(--transition-fast);
}

.toast__close:hover {
  color: var(--bone);
}

/* 进出场：从右侧滑入并淡入 */
.toast-enter-active,
.toast-leave-active {
  transition:
    opacity var(--transition-fast),
    transform var(--transition-fast);
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateX(12px);
}

/* 移动端贴到两侧，不再只占右下角那一条 */
@media (max-width: 560px) {
  .toasts {
    right: 12px;
    left: 12px;
    bottom: 12px;
    max-width: none;
  }
}

/* 尊重"减少动效"偏好：关掉滑动，只保留显隐 */
@media (prefers-reduced-motion: reduce) {
  .toast-enter-from,
  .toast-leave-to {
    transform: none;
  }
}
</style>
