<script setup lang="ts">
/**
 * 复选框。
 *
 * ## 为什么要自己画
 *
 * 原生复选框的外观由浏览器与操作系统决定，样式几乎无法用设计令牌控制 ——
 * 同一套暗色界面里会冒出一个系统配色的方框。这里保留真实的
 * `<input type="checkbox">` 承载语义与键盘操作（只是视觉上隐藏），外观另画。
 *
 * ## 指针事件为什么必须挂在**根元素**上
 *
 * 曾经把 `@pointerdown` 挂在那个隐藏的 `<input>` 上，结果**滑动多选一次都没生效**：
 * 输入框只有 1px 且被 `clip-path` 裁掉，真实点击落在旁边的方块上，事件根本不经过
 * 它。复选框本身还能用，是因为 `<label>` 的原生转发触发了 `change` —— 走的完全是
 * 另一条路径，所以看不出问题。
 *
 * 顺带把外层从 `<label>` 换成了 `<div>`：label 会把点击转发给输入框，和这里的
 * 处理叠加就成了**一次按下翻转两次**。去掉转发，状态只由这一处决定。
 *
 * ## 滑动多选
 *
 * 分工：这里只报"按下了""被离开了、被滑过了"，**拖动由列表统筹** —— 每个复选框
 * 并不知道自己的兄弟。
 */
import { ref } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    /** 无障碍名称。没有可见文字时必须给 */
    label?: string
    disabled?: boolean
  }>(),
  { label: undefined, disabled: false },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  /** 指针按下，带上这次按下后的值。父级据此记下起点与"要刷成什么值" */
  press: [value: boolean]
}>()

const input = ref<HTMLInputElement | null>(null)

function onPointerDown(event: PointerEvent): void {
  if (props.disabled) return

  // 必须阻止默认行为：否则拖动过程会一路选中沿途的文字
  event.preventDefault()

  // 上面拦掉了默认行为，鼠标点击就不会再给输入框焦点，这里补回来
  input.value?.focus()

  const next = !props.modelValue
  emit('update:modelValue', next)
  emit('press', next)
}

function onChange(event: Event): void {
  // 鼠标路径已被 preventDefault 拦住，走到这里的只可能是键盘（空格）
  if (props.disabled) return
  emit('update:modelValue', (event.target as HTMLInputElement).checked)
}
</script>

<template>
  <div
    class="checkbox"
    :class="{ 'checkbox--on': modelValue, 'checkbox--off': disabled }"
    @pointerdown="onPointerDown"
  >
    <input
      ref="input"
      class="checkbox__input"
      type="checkbox"
      :checked="modelValue"
      :disabled="disabled"
      :aria-label="label"
      @change="onChange"
    />
    <span class="checkbox__box" aria-hidden="true" />
  </div>
</template>

<style scoped>
.checkbox {
  /* 定位上下文：隐藏的输入框靠它把自己关在这一格内，见下面 .checkbox__input */
  position: relative;
  display: inline-flex;
  align-items: center;
  /* 不加这句，按住拖动会一路选中沿途的文字 */
  user-select: none;
  cursor: pointer;
}

/*
  视觉上藏起来，但**不能 display:none 或 visibility:hidden** —— 那样键盘就找不到
  它了，而它正是语义与空格键的载体。

  **外面必须有定位祖先**（见 `.checkbox` 的 `position: relative`）。没有的话它的
  包含块是初始包含块，而 `position: absolute` 的元素**不受任何祖先的 overflow
  裁剪** —— 列表里成百个隐藏输入框按静态位置一路铺下去，会把整个文档撑得比视口
  还高。表现是"整页还能滚，滚下去是一片空白"，且完全看不出跟复选框有关。
*/
.checkbox__input {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: 0;
  padding: 0;
  border: 0;
  clip-path: inset(50%);
  overflow: hidden;
}

.checkbox__box {
  width: 18px;
  height: 18px;
  display: grid;
  place-items: center;
  background: var(--bg);
  border: 1px solid var(--line-strong);
  border-radius: 4px;
  transition:
    background var(--transition-fast),
    border-color var(--transition-fast);
}

/* 用两条边框拼出对勾，不引入图标字体或 SVG —— 只有这一个形状，不值得 */
.checkbox__box::after {
  content: '';
  width: 4px;
  height: 8px;
  margin-top: -2px;
  border: solid var(--bg);
  border-width: 0 2px 2px 0;
  transform: rotate(45deg) scale(0);
  transition: transform var(--transition-fast);
}

.checkbox:hover .checkbox__box {
  border-color: var(--mute);
}

.checkbox--on .checkbox__box {
  background: var(--red);
  border-color: var(--red);
}

.checkbox--on .checkbox__box::after {
  transform: rotate(45deg) scale(1);
}

/* 焦点画在方块上，因为真正的输入框是看不见的 */
.checkbox__input:focus-visible + .checkbox__box {
  border-color: var(--red-hi);
  outline: 2px solid var(--red-hi);
  outline-offset: 3px;
}

.checkbox--off {
  cursor: not-allowed;
  opacity: 0.45;
}
</style>
