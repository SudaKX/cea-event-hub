<script setup lang="ts">
/**
 * 自定义下拉选择。
 *
 * ## 为什么要自己写
 *
 * 原生 `<select>` 的**展开列表由操作系统绘制**，CSS 管不到。暗色界面下点开一个
 * 原生 select，弹出的往往是一块浅色系统菜单，与整套海报配色割裂。这是唯一动机，
 * 也是唯一的收益 —— 其余方面原生控件都更好。
 *
 * ## 因此必须把原生控件白送的东西补回来
 *
 * 自己写等于接手了原生控件的全部职责。以下每一条都不是可选项：
 *
 * - **键盘**：`↓`/`↑` 移动、`Home`/`End` 首尾、`Enter`/`Space` 选中、`Esc` 关闭
 *   且不改变选择、`Tab` 关闭并把焦点交给下一个控件。打开时焦点**留在按钮上**，
 *   靠 `aria-activedescendant` 告知读屏当前项 —— 这是 select-only combobox 的
 *   标准做法，比把焦点移进列表更稳（不会丢焦点、不用管 Tab 顺序）。
 * - **首字母跳转**：连打字母按标签前缀查找，与原生行为一致。
 * - **鼠标**：点外部关闭、悬停即高亮。
 * - **视口翻转**：下方空间不足时向上展开，否则靠近页面底部的下拉会被裁掉。
 *
 * ## 与原生控件的差距（已知且接受）
 *
 * 移动端的原生滚轮选择器没有了；Windows 高对比度模式下的系统配色也拿不到。
 * 这两点是为了视觉一致付出的代价。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId } from 'vue'

export interface SelectOption {
  value: string
  label: string
  disabled?: boolean
}

const props = withDefaults(
  defineProps<{
    modelValue: string
    options: SelectOption[]
    /** 可见标签。给了就渲染成 .field 结构，并作为无障碍名称 */
    label?: string
    placeholder?: string
    disabled?: boolean
    /** 没有可见标签时的无障碍名称 */
    ariaLabel?: string
  }>(),
  {
    label: undefined,
    placeholder: '请选择',
    disabled: false,
    ariaLabel: undefined,
  },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const root = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
const list = ref<HTMLElement | null>(null)

const open = ref(false)
const activeIndex = ref(-1)
/** 向上展开。打开时按视口空间测量一次 */
const dropUp = ref(false)

const uid = useId()
const listId = computed(() => `${uid}-list`)
const labelId = computed(() => `${uid}-label`)
const optionId = (index: number) => `${uid}-opt-${index}`

const selectedIndex = computed(() =>
  props.options.findIndex((option) => option.value === props.modelValue),
)

const selectedLabel = computed(() => props.options[selectedIndex.value]?.label ?? '')

const activeDescendant = computed(() =>
  open.value && activeIndex.value >= 0 ? optionId(activeIndex.value) : undefined,
)

/** 无障碍名称：优先来自可见标签，没有标签时才用 ariaLabel */
const nameFrom = computed(() => (props.label ? labelId.value : undefined))
const nameText = computed(() => (props.label ? undefined : props.ariaLabel))

/** 找第一个可用项。`delta` 为负时从末尾往前找。 */
function firstEnabled(delta = 1): number {
  const count = props.options.length
  for (let step = 0; step < count; step += 1) {
    const index = delta > 0 ? step : count - 1 - step
    if (!props.options[index]?.disabled) return index
  }
  return -1
}

async function openMenu(preferLast = false): Promise<void> {
  if (props.disabled || open.value) return
  open.value = true

  activeIndex.value =
    selectedIndex.value >= 0 ? selectedIndex.value : firstEnabled(preferLast ? -1 : 1)

  await nextTick()
  measureDirection()
  scrollActiveIntoView()
}

function closeMenu(restoreFocus = true): void {
  if (!open.value) return
  open.value = false
  dropUp.value = false
  if (restoreFocus) trigger.value?.focus()
}

/**
 * 下方空间不够就向上展开。
 *
 * 只在向上确实比向下宽敞时才翻，否则宁可向下并让列表内部滚动 ——
 * 触发器贴着视口底部时，向上翻也救不了多少。
 */
function measureDirection(): void {
  const triggerEl = trigger.value
  const listEl = list.value
  if (!triggerEl || !listEl) return

  const rect = triggerEl.getBoundingClientRect()
  const spaceBelow = window.innerHeight - rect.bottom
  const needed = listEl.offsetHeight + 8
  dropUp.value = spaceBelow < needed && rect.top > spaceBelow
}

function scrollActiveIntoView(): void {
  const el = list.value?.querySelector<HTMLElement>('[data-active="true"]')
  // happy-dom 等环境可能没有实现；缺了只是不滚动，不该让组件报错
  el?.scrollIntoView?.({ block: 'nearest' })
}

/** 按方向移到下一个可用项，跳过禁用项并环绕。 */
function move(delta: number): void {
  const count = props.options.length
  if (count === 0) return

  if (activeIndex.value < 0) {
    activeIndex.value = firstEnabled(delta)
    void nextTick(scrollActiveIntoView)
    return
  }

  let index = activeIndex.value
  for (let step = 0; step < count; step += 1) {
    index = (index + delta + count) % count
    if (!props.options[index]?.disabled) {
      activeIndex.value = index
      void nextTick(scrollActiveIntoView)
      return
    }
  }
}

function choose(option: SelectOption): void {
  if (option.disabled) return
  if (option.value !== props.modelValue) emit('update:modelValue', option.value)
  closeMenu()
}

function commitActive(): void {
  const option = props.options[activeIndex.value]
  if (option) choose(option)
}

function hover(index: number, option: SelectOption): void {
  if (option.disabled) return
  activeIndex.value = index
}

/**
 * 首字母跳转。
 *
 * **搜索始终从列表开头开始**，"打前缀"就落到第一项；重复按同一个字母则在同首
 * 字母的候选项之间轮换（与原生一致）。若改成"从当前项之后开始找"，打开后按一个
 * 字母会跳到第二个匹配项 —— 用户打首字母的预期是"找到它"，不是"找下一个它"。
 *
 * 无障碍名称由 `aria-labelledby` 给出，所以外层不需要（也不能）用 `<label>`：
 * `<label>` 会把点击转发给内部的按钮，点选项时会把它再点一次。
 */
let typed = ''
let typedTimer: ReturnType<typeof setTimeout> | undefined

function typeAhead(char: string): void {
  const lower = char.toLowerCase()
  // 连按同一个字母 = 轮换，而不是把缓冲变成 "aa"（那什么也匹配不到）
  const cycling = typed.length > 0 && [...typed].every((item) => item === lower)
  typed = cycling ? lower : typed + lower

  clearTimeout(typedTimer)
  typedTimer = setTimeout(() => {
    typed = ''
  }, 500)

  const count = props.options.length
  if (count === 0) return

  const start = cycling ? activeIndex.value + 1 : 0
  for (let step = 0; step < count; step += 1) {
    const index = (start + step + count) % count
    const option = props.options[index]
    if (option && !option.disabled && option.label.toLowerCase().startsWith(typed)) {
      if (!open.value) void openMenu()
      activeIndex.value = index
      void nextTick(scrollActiveIntoView)
      return
    }
  }
}

function onTriggerKeydown(event: KeyboardEvent): void {
  if (props.disabled) return

  switch (event.key) {
    case 'ArrowDown':
    case 'ArrowUp': {
      event.preventDefault()
      const delta = event.key === 'ArrowDown' ? 1 : -1
      if (open.value) move(delta)
      // 关闭状态下 ↑ 与原生一致：打开并落到最末可用项
      else void openMenu(delta === -1)
      return
    }
    case 'Enter':
    case ' ':
      event.preventDefault()
      if (open.value) commitActive()
      else void openMenu()
      return
    case 'Escape':
      if (open.value) {
        event.preventDefault()
        closeMenu()
      }
      return
    case 'Home':
      if (open.value) {
        event.preventDefault()
        activeIndex.value = firstEnabled(1)
        void nextTick(scrollActiveIntoView)
      }
      return
    case 'End':
      if (open.value) {
        event.preventDefault()
        activeIndex.value = firstEnabled(-1)
        void nextTick(scrollActiveIntoView)
      }
      return
    case 'Tab':
      // 关闭但**不抢回焦点** —— 用户明确要走了
      closeMenu(false)
      return
    default:
      if (event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) {
        typeAhead(event.key)
      }
  }
}

function onDocumentPointerDown(event: MouseEvent): void {
  if (!open.value) return
  const target = event.target as Node | null
  if (target && root.value?.contains(target)) return
  closeMenu(false)
}

onMounted(() => document.addEventListener('mousedown', onDocumentPointerDown))
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocumentPointerDown)
  clearTimeout(typedTimer)
})
</script>

<template>
  <!--
    单份模板。带 label 时外层是个普通的 .field 容器（**刻意不用 `<label>`**：
    它会把点击转发给内部的按钮，点选项时等于把按钮又点了一次，菜单会被重新
    打开）。无障碍名称走 aria-labelledby。
    ref 必须挂在这个根节点上，否则"点外部关闭"判断不到自身。
  -->
  <div ref="root" :class="label ? 'field' : 'select-root'">
    <span v-if="label" :id="labelId" class="field__label">{{ label }}</span>

    <span class="select" :class="{ 'select--open': open, 'select--disabled': disabled }">
      <button
        ref="trigger"
        type="button"
        class="input select__trigger"
        role="combobox"
        aria-haspopup="listbox"
        :aria-expanded="open"
        :aria-controls="listId"
        :aria-activedescendant="activeDescendant"
        :aria-labelledby="nameFrom"
        :aria-label="nameText"
        :disabled="disabled"
        @click="open ? closeMenu() : openMenu()"
        @keydown="onTriggerKeydown"
      >
        <span class="select__value" :class="{ 'select__value--empty': !selectedLabel }">
          {{ selectedLabel || placeholder }}
        </span>
        <span class="select__caret" aria-hidden="true" />
      </button>

      <ul
        v-if="open"
        :id="listId"
        ref="list"
        class="select__list"
        :class="{ 'select__list--up': dropUp }"
        role="listbox"
        :aria-labelledby="nameFrom"
        :aria-label="nameText"
      >
        <li
          v-for="(option, index) in options"
          :id="optionId(index)"
          :key="option.value"
          class="select__option"
          :class="{
            'select__option--active': index === activeIndex,
            'select__option--selected': option.value === modelValue,
            'select__option--disabled': option.disabled,
          }"
          :data-active="index === activeIndex"
          role="option"
          :aria-selected="option.value === modelValue"
          :aria-disabled="option.disabled || undefined"
          @click="choose(option)"
          @mouseenter="hover(index, option)"
        >
          {{ option.label }}
        </li>
      </ul>
    </span>
  </div>
</template>

<style scoped>
.select-root,
.select {
  display: block;
  width: 100%;
}

.select {
  position: relative;
}

/* 外观复用 .input（共享样式里与 input/textarea 同一套），
   这里只补它作为按钮需要的那几项 */
.select__trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  text-align: left;
  cursor: pointer;
}

.select__trigger:disabled {
  cursor: not-allowed;
  color: var(--dim);
}

.select__value {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 占位态用更暗的颜色，与 input 的 ::placeholder 对齐 */
.select__value--empty {
  color: var(--dim);
}

/* 用两条边转出一个尖角，而不是引图标字体或 SVG */
.select__caret {
  flex: none;
  width: 7px;
  height: 7px;
  margin-top: -3px;
  border-right: 1.5px solid var(--mute);
  border-bottom: 1.5px solid var(--mute);
  transform: rotate(45deg);
  transition: transform var(--transition-fast);
}

.select--open .select__caret {
  transform: rotate(-135deg) translate(-2px, -2px);
  border-color: var(--red-hi);
}

.select__list {
  position: absolute;
  z-index: 20;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  margin: 0;
  padding: 4px;
  list-style: none;
  max-height: 260px;
  overflow-y: auto;
  background: var(--panel);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-surface);
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.45);
}

.select__list--up {
  top: auto;
  bottom: calc(100% + 4px);
}

.select__option {
  padding: 8px 10px;
  border-radius: var(--radius-control);
  font: 500 13px/1.5 var(--mono);
  color: var(--bone);
  cursor: pointer;
}

/* 键盘活动项与鼠标悬停共用一种高亮，避免两套指示同时出现 */
.select__option--active {
  background: rgba(255, 255, 255, 0.06);
}

.select__option--selected {
  color: var(--red-hi);
}

/* 选中项左侧一道红条：与侧栏当前项的标记方式一致 */
.select__option--selected::before {
  content: '';
  display: inline-block;
  width: 2px;
  height: 0.9em;
  margin-right: 7px;
  vertical-align: -0.08em;
  background: var(--red);
}

.select__option--disabled {
  color: var(--dim);
  cursor: not-allowed;
}
</style>
