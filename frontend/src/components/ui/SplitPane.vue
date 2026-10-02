<script setup lang="ts">
/**
 * 左右分栏，中间的分隔线可以拖。
 *
 * 侧栏宽度是**受控**的（`v-model`）：宽度属于调用方的状态，这个组件只负责把拖动
 * 换算成新宽度报上去。这样刷新、切换视图时怎么处理宽度，由调用方一处决定。
 *
 * 分隔线用 `role="separator"` + 方向键：纯拖拽对键盘用户等于没有这个功能。
 */
import { computed, onBeforeUnmount, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 侧栏宽度（像素） */
    modelValue: number
    min?: number
    /** 绝对上限 */
    max?: number
    /** 相对上限：侧栏最多占容器的这个比例，免得把主区挤没 */
    maxRatio?: number
  }>(),
  { min: 220, max: 720, maxRatio: 0.6 },
)

const emit = defineEmits<{ 'update:modelValue': [value: number] }>()

const root = ref<HTMLElement | null>(null)
const dragging = ref(false)

let startX = 0
let startWidth = 0

/** 容器窄的时候，绝对上限可能比容器还宽，所以两个上限取小的那个 */
const upperBound = computed(() => {
  const width = root.value?.clientWidth ?? 0
  return width > 0 ? Math.min(props.max, Math.round(width * props.maxRatio)) : props.max
})

function clamp(value: number): number {
  return Math.min(upperBound.value, Math.max(props.min, Math.round(value)))
}

function onDown(event: PointerEvent): void {
  event.preventDefault()
  dragging.value = true
  startX = event.clientX
  startWidth = props.modelValue
  // 听 window 而不是分隔线自己：指针拖到外面是常事
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
}

function onMove(event: PointerEvent): void {
  // 往左拖 = 侧栏变宽，所以是减
  emit('update:modelValue', clamp(startWidth - (event.clientX - startX)))
}

function onUp(): void {
  dragging.value = false
  window.removeEventListener('pointermove', onMove)
  window.removeEventListener('pointerup', onUp)
}

function onKeydown(event: KeyboardEvent): void {
  // 方向键往哪边，分隔线就往哪边 —— 与拖动方向一致
  const step = event.shiftKey ? 40 : 10
  if (event.key === 'ArrowLeft') emit('update:modelValue', clamp(props.modelValue + step))
  else if (event.key === 'ArrowRight') emit('update:modelValue', clamp(props.modelValue - step))
  else return
  event.preventDefault()
}

onBeforeUnmount(onUp)
</script>

<template>
  <div ref="root" class="split" :class="{ 'split--dragging': dragging }">
    <div class="split__main">
      <slot />
    </div>

    <div
      class="split__handle"
      role="separator"
      aria-orientation="vertical"
      aria-label="调整侧栏宽度"
      :aria-valuenow="modelValue"
      :aria-valuemin="min"
      :aria-valuemax="upperBound"
      tabindex="0"
      @pointerdown="onDown"
      @keydown="onKeydown"
    >
      <span class="split__grip" aria-hidden="true" />
    </div>

    <aside class="split__side" :style="{ width: `${modelValue}px` }">
      <slot name="side" />
    </aside>
  </div>
</template>

<style scoped>
.split {
  display: flex;
  align-items: stretch;
  min-width: 0;
}

/*
  `min-width: 0` 不能少：flex 子项默认 min-width:auto，里面的表格再宽也不会收缩，
  于是主区会顶着侧栏一起溢出。
*/
.split__main {
  flex: 1;
  min-width: 0;
}

.split__handle {
  flex: none;
  width: 12px;
  display: grid;
  place-items: center;
  cursor: col-resize;
  /* 拖动时不要选中文字 */
  user-select: none;
  touch-action: none;
}

/* 平时只是一条发丝线，抓取区比它宽得多 —— 看得见的细，摸得到的粗 */
.split__grip {
  width: 1px;
  height: 100%;
  background: var(--line);
  transition: background var(--transition-fast);
}

.split__handle:hover .split__grip,
.split__handle:focus-visible .split__grip,
.split--dragging .split__grip {
  width: 2px;
  background: var(--red-hi);
}

.split__handle:focus-visible {
  outline: 2px solid var(--red-hi);
  outline-offset: -4px;
  border-radius: var(--radius-control);
}

.split__side {
  flex: none;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
</style>
