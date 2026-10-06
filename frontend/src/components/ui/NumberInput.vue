<script setup lang="ts">
/**
 * 数字输入：左减、中间直接打字、右加。
 *
 * **为什么不用 `type="number"`。** 原生的上下箭头在暗色界面里几乎看不见，而且只在
 * 悬停时才出现；更要紧的是它在不同浏览器里的行为不一致（滚轮会不会改值、非法输入
 * 怎么报错都各写各的）。这些字段要的是"一个能改的数"，两侧按钮把它变得明确。
 *
 * **中间仍然是可打字的输入框**，而不是只读显示 —— 要跳到 200 不该按两百次加号。
 * 因此用 `type="text"` + `inputmode="numeric"`：没有原生 spinner，手机上仍然弹数字键盘。
 *
 * 值始终是**数字**（除非 `nullable`）：打字过程中允许空串与中间态，但一旦能解析就立刻
 * 夹到 `[min, max]` 并回写；失焦时若为空则落回 `null`（可空时）或 `min`。这样父组件
 * 拿到的永远是合法值，不必在每个调用点再判一次。
 *
 * **调用方必须用 `<div class="field">` 包它，标题写成 `<label :for="id">`。**
 *
 * 不要用 `<label>` 把整个组件裹起来 —— 那会踩两个坑，都在真浏览器里复现过：
 *
 *   1. **悬浮会串**。`<label>` 的悬浮会传播给它的**被标注控件**，而这里是内部第一个
 *      可标注元素，也就是 `−` 按钮。于是鼠标停在 `+` 上时，`−` 也进入 `:hover`，
 *      两个按钮一起高亮。
 *   2. **点标题会改数**。`<label>` 会把点击转发给同一个元素 —— 点「有效期（天）」
 *      这行字，数字就减一。
 *
 * 根因是"按钮排在输入框前面"，而 `<label>` 认的是**第一个**可标注后代。显式 `for`
 * 指向输入框的 `id` 就没有这两个问题：label 不再是祖先，点击照旧聚焦输入框。
 *
 * 别的字段（`<label class="field">` 里直接放 `<input>`）不受影响：那里第一个可标注
 * 元素就是输入框本身。
 */
import { computed, ref, useId, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue: number | null
    /** 读屏用；视觉上的标题由调用方的 `<label :for="id">` 提供 */
    label: string
    /**
     * 输入框的 id。**调用方的 `<label for>` 要指向它**。
     * 不传则自动生成一个唯一值（Vue 3.5 的 `useId`）。
     */
    id?: string
    min?: number
    max?: number
    step?: number
    disabled?: boolean
    /** 允许为空。空 = 调用方自己解释（例如"不限"） */
    nullable?: boolean
    /** 可空时，从空开始点加减所落到的值 */
    nullBase?: number
  }>(),
  {
    id: undefined,
    min: 0,
    max: Number.MAX_SAFE_INTEGER,
    step: 1,
    disabled: false,
    nullable: false,
    nullBase: undefined,
  },
)

/** 自动 id 与调用方给的一起用：没有显式 id 时也要能被 `<label for>` 指到 */
const autoId = useId()
const inputId = computed(() => props.id ?? `number-${autoId}`)

/**
 * 输入框的无障碍名。
 *
 * **只在没有外部 `<label for>` 时才挂 `aria-label`** —— 它的优先级高于 `<label for>`，
 * 两边都写会让可见的那行字失效（读屏念的是 aria-label，屏幕上写的是另一个）。
 * 调用方给了 `id` 就说明它会写 `<label :for>`，这时交给那个 label。
 *
 * 两侧按钮仍然用 `label` 拼出"减少/增加 X"：那是它们唯一的名称来源。
 */
const inputAriaLabel = computed(() => (props.id ? undefined : props.label))

const emit = defineEmits<{ 'update:modelValue': [value: number | null] }>()

/** 打字中的原文。与 `modelValue` 分开，否则清空输入框会立刻被夹回 min，删不掉 */
const text = ref(props.modelValue === null ? '' : String(props.modelValue))

watch(
  () => props.modelValue,
  (value) => {
    const shown = value === null ? '' : String(value)
    if (text.value !== shown && Number(text.value) !== value) text.value = shown
  },
)

function clamp(value: number): number {
  if (Number.isNaN(value)) return props.min
  return Math.min(Math.max(value, props.min), props.max)
}

const canDecrease = computed(
  () => !props.disabled && (props.modelValue === null || props.modelValue > props.min),
)
const canIncrease = computed(
  () => !props.disabled && (props.modelValue === null || props.modelValue < props.max),
)

function bump(delta: number): void {
  const base =
    props.modelValue === null ? (props.nullBase ?? props.min) : props.modelValue
  // 从空往上加时，`base` 本身就是想要的那个值，不再叠一次步长
  const next =
    props.modelValue === null && delta > 0 ? clamp(base) : clamp(base + delta * props.step)
  text.value = String(next)
  emit('update:modelValue', next)
}

function onInput(event: Event): void {
  const raw = (event.target as HTMLInputElement).value
  text.value = raw
  // 允许"正在打字"的中间态：空串、只有负号时不回写，等它能解析成数字再说
  const parsed = Number(raw)
  if (raw.trim() === '') {
    if (props.nullable) emit('update:modelValue', null)
    return
  }
  if (Number.isNaN(parsed)) return
  emit('update:modelValue', clamp(parsed))
}

function onBlur(): void {
  if (text.value.trim() === '') {
    text.value = ''
    if (props.nullable) emit('update:modelValue', null)
    else emit('update:modelValue', clamp(props.min))
    return
  }
  const parsed = Number(text.value)
  const settled = clamp(Number.isNaN(parsed) ? props.min : parsed)
  text.value = String(settled)
  emit('update:modelValue', settled)
}
</script>

<template>
  <div class="stepper" :class="{ 'stepper--disabled': disabled }">
    <button
      class="stepper__btn"
      type="button"
      :disabled="!canDecrease"
      :aria-label="`减少${label}`"
      @click="bump(-1)"
    >
      −
    </button>
    <input
      :id="inputId"
      class="stepper__input num"
      type="text"
      inputmode="numeric"
      autocomplete="off"
      :value="text"
      :disabled="disabled"
      :aria-label="inputAriaLabel"
      @input="onInput"
      @blur="onBlur"
    />
    <button
      class="stepper__btn"
      type="button"
      :disabled="!canIncrease"
      :aria-label="`增加${label}`"
      @click="bump(1)"
    >
      +
    </button>
  </div>
</template>

<style scoped>
.stepper {
  display: flex;
  align-items: stretch;
  height: var(--control-height);
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
  background: var(--bg);
  overflow: hidden;
  transition: border-color var(--transition-fast);
}

/*
  **聚焦时描红的是外框，不是中间那段。**

  这是刻意的分工：中间那段是"可以直接打字的数字"，不是表单里的独立输入框，它自己不
  该有框、圆角与聚焦边框（那三样都是它曾经从全局输入框规则里继承来的，见下面
  `.stepper__input` 的说明）。"这个控件正在被编辑"这件事由整个组合控件表达 —— 所以
  `:focus-within` 描的是外框。两者是一套，去掉其中一半就不成立了。
*/
.stepper:focus-within {
  border-color: var(--red-hi);
}

.stepper--disabled {
  opacity: 0.5;
}

.stepper__btn {
  flex: none;
  width: 40px;
  border: 0;
  background: var(--select-soft);
  color: var(--bone);
  font: 600 16px/1 var(--mono);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.stepper__btn:hover:not(:disabled) {
  background: var(--red-soft);
  color: var(--red-hi);
}

.stepper__btn:disabled {
  color: var(--dim);
  cursor: not-allowed;
}

/*
  中间可打字：**没有自己的框、圆角与聚焦边框**。

  它已经在外层那个带边框的组合控件里，再套一层就是重复 —— 而且那层的 10px 圆角嵌在
  外框的 7px 圆角里，视觉上本来就不对。（原先它确实带着这些，来自 components.css 的
  全局输入框规则，特异度比这里的 scoped 样式高；已在源头把它排除。）

  聚焦的红色边框因此落在**外框**上（见上面的 `:focus-within`），中间那段保持平的。
*/
.stepper__input {
  flex: 1;
  min-width: 0;
  width: 100%;
  min-height: 0;
  padding: 0 10px;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--bone);
  font: 500 14px/1 var(--mono);
  text-align: center;
}

.stepper__input:focus {
  outline: none;
  border: 0;
}

/* 中间与两侧之间各一条发丝分隔线，让三段结构看得出来 */
.stepper__btn:first-child {
  border-right: 1px solid var(--line);
}

.stepper__btn:last-child {
  border-left: 1px solid var(--line);
}
</style>
