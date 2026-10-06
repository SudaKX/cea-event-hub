<script setup lang="ts">
/**
 * 开关。
 *
 * **为什么不用裸 checkbox。** 平台开关是"立即生效的应急动作"，与"在表单里勾选一项"
 * 是两种东西：前者要能一眼看出当前状态、且点下去就有后果。所以它有独立的轨道与滑块，
 * 状态用颜色表达，而不是一个 12px 的方框。
 *
 * **无障碍上它仍然是 checkbox。** 保留原生 `<input type="checkbox">` 并加
 * `role="switch"`：键盘、焦点、读屏的"已选中/未选中"全都白拿，不必自己实现一套
 * （自己搓一个 div 开关通常就在这里出问题）。
 *
 * **DOM 永不自己翻。** 用 `@click.prevent` 挡掉浏览器的默认切换，状态只随 `modelValue`
 * 变化 —— 因为父组件可能要先弹确认框，而**取消时开关不能已经翻过去**。若让它自己翻，
 * Vue 在值没变时不会回写 DOM，界面就会停在一个与服务端不一致的位置上。
 *
 * **两种用法，都在这个前提下成立：**
 *
 *   - `v-model`：普通表单字段。组件同时抛 `update:modelValue`，父组件的值立刻跟着变。
 *   - `:model-value` + `@request`：需要先确认的动作（例如"暂停邀请"影响所有人）。
 *     父组件只在确认之后才改值，取消时开关原地不动。
 */
const props = defineProps<{
  modelValue: boolean
  /** 读屏用的名称；视觉上的说明由调用方自己排版 */
  label: string
  disabled?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  /** 只表示"被点了"，要不要改值由父组件决定 */
  request: []
}>()

function onClick(): void {
  // 两个都抛：`v-model` 的父组件收到新值，`@request` 的父组件自行决定
  emit('update:modelValue', !props.modelValue)
  emit('request')
}
</script>

<template>
  <label class="switch" :class="{ 'switch--on': modelValue, 'switch--off': !modelValue }">
    <input
      class="switch__input"
      type="checkbox"
      role="switch"
      :checked="modelValue"
      :disabled="disabled"
      :aria-label="label"
      @click.prevent="onClick"
      @keydown.space.prevent="onClick"
      @keydown.enter.prevent="onClick"
    />
    <span class="switch__track" aria-hidden="true">
      <span class="switch__thumb" />
    </span>
  </label>
</template>

<style scoped>
.switch {
  flex: none;
  /* 里面那个视觉隐藏的 input 是 absolute —— 它需要这一层做定位参照 */
  position: relative;
  display: inline-flex;
  align-items: center;
  cursor: pointer;
}

/* 原生 input 只留可聚焦与可读屏，视觉交给轨道 */
.switch__input {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  border: 0;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

.switch__track {
  display: flex;
  align-items: center;
  width: 44px;
  height: 24px;
  padding: 0 4px;
  /*
    **六边形，且与外轨的斜边同角度。**

    `polygon(25% 0, 75% 0, …)` 只有在盒子宽高比 = 2:√3 时才是正六边形。外轨是宽扁的
    （44×24），照抄 25% 会让它的斜边比滑块平得多 —— 两个六边形看起来不是同一种形状。

    顶点内缩量由斜边角度决定：正六边形的斜边与竖直方向成 30°，于是
    `a = (h/2)·tan30° = h/(2√3)`。外轨 24 高 → a = 6.93px → 6.93/44 = **15.75%**。
    滑块是 15×13（13×2/√3 = 15.01，即 2:√3），a = 3.75px → 3.75/15 = **25%**。
    两者斜边角度因此一致，滑块本身是正六边形。

    `clip-path` 会把边框一起裁掉，所以这里**不用边框**，靠底色对比表达状态 ——
    关是中性灰、开是强调红，滑块两边都保持骨白。
  */
  clip-path: polygon(15.75% 0, 84.25% 0, 100% 50%, 84.25% 100%, 15.75% 100%, 0 50%);
  background: var(--dim);
  transition: background var(--transition-fast);
}

.switch__thumb {
  display: block;
  /*
    **尺寸不能随手写**：宽 = 高 × 2/√3，否则 25% 的顶点内缩给出来的就不是正六边形。
    18 × 2/√3 = 20.78 —— 占轨道高的 75%，上下各余 3px。
    （`tokens.spec.ts` 里有一条断言守着这个比例，写错了会直接红。）
  */
  width: 20.78px;
  height: 18px;
  clip-path: polygon(25% 0, 75% 0, 100% 50%, 75% 100%, 25% 100%, 0 50%);
  background: var(--bone);
  transition:
    transform var(--transition-fast),
    background var(--transition-fast);
}

/* 开：整个轨道换成强调红，滑块右移。两处一起变，状态才读得出来 */
.switch--on .switch__track {
  background: var(--red);
}

/* 行程 = 轨道宽 − 左右内边距 − 滑块宽 = 44 − 8 − 20.78 = 15.22 */
.switch--on .switch__thumb {
  transform: translateX(15.22px);
}

/*
  悬停与焦点都走 `filter: drop-shadow` 而不是 `outline` / `border-color`：
  **它跟着裁剪后的形状走**，于是高亮也是六边形的。矩形描边套在六边形上会很出戏。
*/
.switch:hover .switch__track {
  filter: drop-shadow(0 0 3px var(--red-hi));
}

.switch__input:focus-visible + .switch__track {
  filter: drop-shadow(0 0 4px var(--red-hi));
}

.switch__input:disabled + .switch__track {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
