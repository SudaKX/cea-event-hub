<script setup lang="ts">
/**
 * 表格单元格：定宽、截断、可展开看全文。
 *
 * ## 为什么需要它
 *
 * 提交内容是一段任意结构的 JSON，长度完全不受控。直接铺进表格会让某一行的某一列
 * 撑到几千像素，整张表因此失去可读性 —— 列宽随内容抖动，扫读时眼睛找不到列。
 * 定宽 + 截断把这件事钉死，代价是内容看不全，所以必须配一个展开入口。
 *
 * ## 两种展开方式，各有各的用处
 *
 * - **悬浮**：快速瞄一眼，鼠标移开就收
 * - **点击**：钉住不关，方便选中复制
 *
 * 悬浮触发点只在按钮与面板上，**不在整个单元格上** —— 否则鼠标横扫一行会让每列
 * 的面板依次弹出，比截断本身更烦人。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 单元格里显示的文本，超出宽度会被截断 */
    text: string
    /** 展开面板里的完整内容。不给就用 `text` */
    detail?: string
    /** 展开按钮的无障碍名称，说明展开的是什么 */
    label?: string
  }>(),
  { detail: undefined, label: '完整内容' },
)

const root = ref<HTMLElement | null>(null)
const anchor = ref<HTMLElement | null>(null)
const panel = ref<HTMLElement | null>(null)

const open = ref(false)
/** 点过之后钉住：鼠标移开也不关，否则没法选中复制 */
const pinned = ref(false)
const dropUp = ref(false)

const uid = useId()
const panelId = computed(() => `${uid}-panel`)
const body = computed(() => props.detail ?? props.text)

/**
 * 下方空间不够就向上展开。
 *
 * 只在向上确实更宽敞时才翻 —— 面板贴着视口底部时，向上翻也救不了多少，
 * 不如让它向下并靠内部滚动。
 */
function measure(): void {
  const anchorEl = anchor.value
  const panelEl = panel.value
  if (!anchorEl || !panelEl) return

  const rect = anchorEl.getBoundingClientRect()
  const spaceBelow = window.innerHeight - rect.bottom
  const needed = Math.min(panelEl.offsetHeight, 320) + 8
  dropUp.value = spaceBelow < needed && rect.top > spaceBelow
}

function show(): void {
  open.value = true
  void nextTick(measure)
}

function hide(): void {
  if (pinned.value) return
  open.value = false
}

function togglePin(): void {
  pinned.value = !pinned.value
  open.value = pinned.value
  if (open.value) void nextTick(measure)
}

function close(): void {
  pinned.value = false
  open.value = false
}

function onDocumentPointerDown(event: MouseEvent): void {
  if (!open.value) return
  const target = event.target as Node | null
  if (target && root.value?.contains(target)) return
  close()
}

function onDocumentKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Escape' || !open.value) return
  event.preventDefault()
  close()
}

onMounted(() => {
  document.addEventListener('mousedown', onDocumentPointerDown)
  document.addEventListener('keydown', onDocumentKeydown)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocumentPointerDown)
  document.removeEventListener('keydown', onDocumentKeydown)
})
</script>

<template>
  <span ref="root" class="cell">
    <span class="cell__text">{{ text }}</span>

    <span ref="anchor" class="cell__anchor" @mouseenter="show" @mouseleave="hide">
      <button
        type="button"
        class="cell__more"
        :aria-label="label"
        :aria-expanded="open"
        :aria-controls="panelId"
        @click="togglePin"
      >
        ⋯
      </button>

      <div
        v-if="open"
        :id="panelId"
        ref="panel"
        class="cell__panel"
        :class="{ 'cell__panel--up': dropUp, 'cell__panel--pinned': pinned }"
      >
        <pre class="cell__body">{{ body }}</pre>
      </div>
    </span>
  </span>
</template>

<style scoped>
.cell {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}

/*
  截断靠这三条。`min-width: 0` 不能少 —— flex 子项默认 min-width:auto，
  没有它文本会把容器撑开，省略号永远不出现。
*/
.cell__text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--mono);
  font-size: 12.5px;
  color: var(--bone);
}

.cell__anchor {
  position: relative;
  flex: none;
  display: inline-flex;
}

/* 平时低调，鼠标移到这一格附近才显出来；键盘聚焦时也要看得见 */
.cell__more {
  width: 22px;
  height: 22px;
  padding: 0;
  display: grid;
  place-items: center;
  border: 1px solid transparent;
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--dim);
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  transition:
    color var(--transition-fast),
    border-color var(--transition-fast);
}

.cell__more:hover,
.cell__more:focus-visible,
.cell__more[aria-expanded='true'] {
  color: var(--red-hi);
  border-color: var(--line-strong);
}

.cell__panel {
  position: absolute;
  z-index: 30;
  top: calc(100% + 4px);
  right: 0;
  width: max-content;
  max-width: min(560px, 70vw);
  background: var(--panel);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-surface);
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.5);
}

.cell__panel--up {
  top: auto;
  bottom: calc(100% + 4px);
}

/* 钉住时给个更实的边框，一眼能看出它不会自己关 */
.cell__panel--pinned {
  border-color: var(--red);
}

.cell__body {
  margin: 0;
  padding: 10px 12px;
  max-height: 320px;
  overflow: auto;
  font-family: var(--mono);
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--bone);
  /* JSON 里有长行时按词换行会难看，直接按字符断 */
  white-space: pre-wrap;
  word-break: break-all;
  text-align: left;
}
</style>
