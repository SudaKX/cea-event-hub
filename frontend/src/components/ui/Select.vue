<script setup lang="ts">
/**
 * 自定义下拉选择，支持两种模式。
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
 *   且不改变选择、`Tab` 关闭并把焦点交给下一个控件。焦点**留在控件本身**，靠
 *   `aria-activedescendant` 告知读屏当前项 —— 这是 combobox 的标准做法，比把
 *   焦点移进列表更稳（不会丢焦点、不用管 Tab 顺序）。
 * - **首字母跳转**（非搜索模式）：连打字母按标签前缀查找，与原生行为一致。
 * - **鼠标**：点外部关闭、悬停即高亮。
 * - **视口翻转**：下方空间不足时向上展开，否则靠近页面底部的下拉会被裁掉。
 * - **超长列表滚动**：见 `.select__list` 的 `max-height` + `overflow-y`，
 *   并且键盘移动时活动项会自动滚入视野。
 *
 * ## 两种模式：为什么搜索框就是触发器本身
 *
 * `searchable` 打开时，触发器从按钮换成文本输入框，输入即过滤（子串匹配，
 * 不区分大小写）。
 *
 * 另一种常见做法是"按钮触发器 + 面板里放一个搜索框"。这里**不采用**，因为那会
 * 逼出两个都想当 combobox 的元素：面板里的输入框持有焦点，而 `aria-expanded` /
 * `aria-activedescendant` 却挂在按钮上 —— 读屏此时根本不会播报当前活动项。
 * 把输入框本身做成 combobox，一个控件一个角色，这条线才说得通。
 *
 * 搜索模式下**打开即清空输入并列出全部**：不打字就是浏览，打字就是搜索。关闭时
 * 输入框恢复显示当前选中项的标签。
 *
 * ## 与原生控件的差距（已知且接受）
 *
 * 移动端的原生滚轮选择器没有了；Windows 高对比度模式下的系统配色也拿不到。
 * 这两点是为了视觉一致付出的代价。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'

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
    /** 候选项多时打开：触发器变成输入框，输入即过滤 */
    searchable?: boolean
  }>(),
  {
    label: undefined,
    placeholder: '请选择',
    disabled: false,
    ariaLabel: undefined,
    searchable: false,
  },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const root = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
const search = ref<HTMLInputElement | null>(null)
const list = ref<HTMLElement | null>(null)

const open = ref(false)
const activeIndex = ref(-1)
/** 向上展开。打开时按视口空间测量一次 */
const dropUp = ref(false)
/** 搜索框里的内容。只在实际输入时有值，关闭后丢弃 */
const query = ref('')

const uid = useId()
const listId = computed(() => `${uid}-list`)
const labelId = computed(() => `${uid}-label`)
const optionId = (index: number) => `${uid}-opt-${index}`

/** 过滤后的候选项。非搜索模式或没有关键词时就是全部 */
const items = computed<SelectOption[]>(() => {
  const needle = query.value.trim().toLowerCase()
  if (!props.searchable || !needle) return props.options
  return props.options.filter((option) => option.label.toLowerCase().includes(needle))
})

const selectedIndex = computed(() =>
  items.value.findIndex((option) => option.value === props.modelValue),
)

const selectedLabel = computed(
  () => props.options.find((option) => option.value === props.modelValue)?.label ?? '',
)

/** 输入框显示什么：搜索中显示关键词，否则显示当前选中项 */
const inputText = computed(() => (open.value ? query.value : selectedLabel.value))

/** 打开且没有关键词时，把当前选中项作为灰字提示，避免忘了自己选了什么 */
const inputPlaceholder = computed(() =>
  open.value ? selectedLabel.value || props.placeholder : props.placeholder,
)

const activeDescendant = computed(() =>
  open.value && activeIndex.value >= 0 ? optionId(activeIndex.value) : undefined,
)

/** 无障碍名称：优先来自可见标签，没有标签时才用 ariaLabel */
const nameFrom = computed(() => (props.label ? labelId.value : undefined))
const nameText = computed(() => (props.label ? undefined : props.ariaLabel))

/** 找第一个可用项。`delta` 为负时从末尾往前找。 */
function firstEnabled(delta = 1): number {
  const count = items.value.length
  for (let step = 0; step < count; step += 1) {
    const index = delta > 0 ? step : count - 1 - step
    if (!items.value[index]?.disabled) return index
  }
  return -1
}

async function openMenu(preferLast = false): Promise<void> {
  if (props.disabled || open.value) return
  open.value = true
  query.value = ''

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
  // 关掉就把关键词丢掉，输入框恢复显示选中项 —— 否则下次打开会带着上次的搜索词
  query.value = ''
  if (restoreFocus && !props.searchable) trigger.value?.focus()
}

/**
 * 下方空间不够就向上展开。
 *
 * 只在向上确实比向下宽敞时才翻，否则宁可向下并让列表内部滚动 ——
 * 触发器贴着视口底部时，向上翻也救不了多少。
 */
function measureDirection(): void {
  const control = props.searchable ? search.value : trigger.value
  const listEl = list.value
  if (!control || !listEl) return

  const rect = control.getBoundingClientRect()
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
  const count = items.value.length
  if (count === 0) return

  if (activeIndex.value < 0) {
    activeIndex.value = firstEnabled(delta)
    void nextTick(scrollActiveIntoView)
    return
  }

  let index = activeIndex.value
  for (let step = 0; step < count; step += 1) {
    index = (index + delta + count) % count
    if (!items.value[index]?.disabled) {
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
  const option = items.value[activeIndex.value]
  if (option) choose(option)
}

function hover(index: number, option: SelectOption): void {
  if (option.disabled) return
  activeIndex.value = index
}

/* ---- 首字母跳转（非搜索模式）：连打字母按前缀查找，500ms 后重置 ---- */
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

  const count = items.value.length
  if (count === 0) return

  // 搜索始终从列表开头开始；"打首字母"的预期是找到它，不是找当前位置之后的下一个
  const start = cycling ? activeIndex.value + 1 : 0
  for (let step = 0; step < count; step += 1) {
    const index = (start + step + count) % count
    const option = items.value[index]
    if (option && !option.disabled && option.label.toLowerCase().startsWith(typed)) {
      if (!open.value) void openMenu()
      activeIndex.value = index
      void nextTick(scrollActiveIntoView)
      return
    }
  }
}

function onSearchInput(event: Event): void {
  query.value = (event.target as HTMLInputElement).value
  // 过滤后原来的活动项可能已经不在了，落到第一个可用项
  activeIndex.value = firstEnabled(1)
}

function onSearchFocus(): void {
  void openMenu()
}

/** 焦点离开整个组件时关闭（例如 Tab 走开），避免留下一个悬空的面板 */
function onFocusOut(event: FocusEvent): void {
  const next = event.relatedTarget as Node | null
  if (next && root.value?.contains(next)) return
  closeMenu(false)
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
      event.preventDefault()
      if (open.value) commitActive()
      else void openMenu()
      return
    case ' ':
      // 搜索模式下空格是正常的输入字符，不能当成"确认"
      if (props.searchable) return
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
      if (
        !props.searchable &&
        event.key.length === 1 &&
        !event.metaKey &&
        !event.ctrlKey &&
        !event.altKey
      ) {
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

/** 候选项变少时别让活动项停在越界位置 */
watch(items, (list_) => {
  if (activeIndex.value >= list_.length) activeIndex.value = list_.length - 1
})

watch(
  () => props.disabled,
  (disabled) => {
    if (disabled) closeMenu(false)
  },
)

onMounted(() => document.addEventListener('mousedown', onDocumentPointerDown))
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocumentPointerDown)
  clearTimeout(typedTimer)
})
</script>

<template>
  <!--
    单份模板。带 label 时外层是个普通的 .field 容器（**刻意不用 `<label>`**：
    它会把点击转发给内部的控件，点选项时等于把按钮又点了一次，菜单会被重新
    打开）。无障碍名称走 aria-labelledby。
    ref 必须挂在这个根节点上，否则"点外部关闭"判断不到自身。
  -->
  <div ref="root" :class="label ? 'field' : 'select-root'" @focusout="onFocusOut">
    <span v-if="label" :id="labelId" class="field__label">{{ label }}</span>

    <div class="select" :class="{ 'select--open': open, 'select--disabled': disabled }">
      <!-- 搜索模式：触发器就是输入框本身，一个控件一个 combobox 角色 -->
      <input
        v-if="searchable"
        ref="search"
        type="text"
        class="input select__trigger select__search"
        role="combobox"
        aria-haspopup="listbox"
        :aria-expanded="open"
        :aria-controls="listId"
        :aria-activedescendant="activeDescendant"
        :aria-labelledby="nameFrom"
        :aria-label="nameText"
        aria-autocomplete="list"
        autocomplete="off"
        :disabled="disabled"
        :value="inputText"
        :placeholder="inputPlaceholder"
        @input="onSearchInput"
        @focus="onSearchFocus"
        @keydown="onTriggerKeydown"
      />

      <button
        v-else
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
      </button>

      <span class="select__caret" aria-hidden="true" @click="open ? closeMenu() : openMenu()" />

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
          v-for="(option, index) in items"
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

        <!-- 搜不到东西时给一句话，而不是一个空气泡 -->
        <li v-if="items.length === 0" class="select__empty" role="presentation">
          没有匹配的选项
        </li>
      </ul>
    </div>
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
   这里只补它作为按钮/输入框需要的那几项 */
.select__trigger {
  display: flex;
  align-items: center;
  gap: 10px;
  text-align: left;
  cursor: pointer;
  /* 给右侧的尖角让位，否则长文本会钻到它底下 */
  padding-right: 34px;
}

.select__trigger:disabled {
  cursor: not-allowed;
  color: var(--dim);
}

/* 搜索模式：输入框自己就是触发器，光标应当是文本光标 */
.select__search {
  cursor: text;
}

.select__search::placeholder {
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

/* 用两条边转出一个尖角，而不是引图标字体或 SVG。
   绝对定位：两种模式的触发器高度不同，这样不用各写一套 */
.select__caret {
  position: absolute;
  top: 50%;
  right: 12px;
  width: 7px;
  height: 7px;
  margin-top: -5px;
  border-right: 1.5px solid var(--mute);
  border-bottom: 1.5px solid var(--mute);
  transform: rotate(45deg);
  transition: transform var(--transition-fast);
  cursor: pointer;
}

.select--open .select__caret {
  margin-top: -2px;
  transform: rotate(-135deg);
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
  /* 超过这个高度就在列表内部滚动，而不是把页面撑长 */
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

.select__empty {
  padding: 10px;
  font-size: 12.5px;
  color: var(--dim);
  text-align: center;
}
</style>
