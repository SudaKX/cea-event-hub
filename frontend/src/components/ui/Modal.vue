<script setup lang="ts">
/**
 * 模态对话框。
 *
 * 用原生 `<dialog>` + `showModal()`，而不是自己搓遮罩层：顶层渲染（不用跟 z-index
 * 打架）、焦点陷阱、`Esc` 关闭、`::backdrop` 全是白送的。手写模态最常漏的是焦点
 * 陷阱，而漏掉之后**键盘用户会 tab 到背后的页面上**，还很难发现。
 *
 * ## `<dialog>` 上绝不能写 `display`
 *
 * 作者样式里的 `display` 会盖掉浏览器默认的 `dialog:not([open]) { display: none }`
 * —— 于是关闭状态的对话框依然被渲染，页面上凭空多出一块空卡片。踩过一次，所以
 * 布局一律放在内层 `.modal__body` 上。
 *
 * ## 为什么 `<dialog>` 始终在 DOM 里
 *
 * `showModal()` 需要一个已挂载的元素，所以不能用 `v-if` 把整块摘掉。内容靠 `open`
 * 控制 —— 关闭时连 DOM 都没有，不存在"看不见但占位"。
 */
import { onMounted, ref, useId, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    /** 对话框标题，同时作为无障碍名称 */
    title: string
    /**
     * 尺寸档。
     *
     * - `md`：默认，适合放表格、文件清单这类宽内容
     * - `sm`：确认框这种只有一两句话的场合。720px 宽配一行字的排版很难看，
     *   而且**确认框要的是一眼看完**，宽了反而要扫视
     */
    size?: 'md' | 'sm'
    /**
     * 是否在标题右侧显示"关闭"按钮。
     *
     * 确认框把它关掉：那里已经有一个语义相同的"取消"，两个按钮做同一件事只会
     * 让人多想一秒。`Esc` 与点遮罩仍然可关，退路一条没少。
     */
    showClose?: boolean
  }>(),
  { size: 'md', showClose: true },
)

const emit = defineEmits<{ close: [] }>()

const dialog = ref<HTMLDialogElement | null>(null)
const titleId = useId()

/**
 * 让开合跟上 `open`。
 *
 * `flush: 'post'` 是必须的：`showModal()` 要求元素已挂载，而模板 ref 在渲染后才
 * 绑上。
 */
function sync(open: boolean): void {
  const element = dialog.value
  if (!element) return
  if (open && !element.open) element.showModal()
  else if (!open && element.open) element.close()
}

watch(() => props.open, sync, { flush: 'post', immediate: true })

// `immediate` 那一次跑在模板 ref 绑定之前（那时 dialog 还是 null），所以挂载后再
// 补一次。少了它，"挂载时就已经是打开状态"会静默地不显示。
onMounted(() => sync(props.open))

/** 点遮罩关闭。`<dialog>` 默认不这么做，但用户的预期是点了外面就该关 */
function onBackdropClick(event: MouseEvent): void {
  if (event.target === dialog.value) emit('close')
}
</script>

<template>
  <!-- `@close` 覆盖 Esc 与 close() 两条路径，统一往上抛 -->
  <dialog
    ref="dialog"
    class="modal"
    :class="`modal--${size}`"
    :aria-labelledby="titleId"
    @close="emit('close')"
    @click="onBackdropClick"
  >
    <div v-if="open" class="modal__body">
      <header class="modal__head">
        <h2 :id="titleId" class="modal__title">{{ title }}</h2>
        <button
          v-if="showClose"
          class="btn btn--ghost btn--small"
          type="button"
          @click="emit('close')"
        >
          关闭
        </button>
      </header>

      <slot />
    </div>
  </dialog>
</template>

<style scoped>
/* 见文件头：这个规则块里不能出现 display */
.modal {
  max-height: 86vh;
  padding: 20px 22px 24px;
  overflow: auto;
  background: var(--panel);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-surface);
  color: var(--bone);
}

.modal--md {
  width: min(720px, 92vw);
}

.modal--sm {
  width: min(440px, 92vw);
}

.modal__body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.modal::backdrop {
  background: var(--overlay);
}

.modal__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.modal__title {
  font-size: 16px;
}
</style>
