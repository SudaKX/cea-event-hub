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
 * ## 两种模式：搜索框就是触发器本身
 *
 * `searchable` 打开时**可以**搜索：输入即过滤（子串匹配，不区分大小写）。
 *
 * 搜索框就是触发器本身，而不是在面板里另放一个输入框。后者会逼出两个都想当
 * combobox 的元素：面板里的输入框持有焦点，而 `aria-expanded` /
 * `aria-activedescendant` 却挂在按钮上 —— 读屏此时根本不会播报当前活动项。
 * 一个控件一个 combobox 角色，这条线才说得通。
 *
 * ## 控件形态跟着"是否可编辑"走
 *
 * **关闭时永远是按钮，打开且可搜索时才换成输入框。** 这一点是踩过坑才定下来的：
 * 只要搜索模式下控件"始终"是输入框，它一聚焦就显示文本光标，选完之后看起来像
 * 还在编辑 —— 无论鼠标选还是键盘选。把"可编辑"与"已选中"交给两个元素表达，
 * 这个矛盾才根本消失，而且鼠标与键盘可以走同一条焦点路径（都交回按钮），
 * 键盘用户选完接着 Tab 也不会丢位置。
 *
 * 代价是**两个元素的默认盒模型不同**，切换时会有高度与配色上的跳变。那几处都在
 * 样式里逐条钉死了（`min-height`、`--has-selection`、尖角的 `transform`），
 * 否则点一下控件就会"闪"一下。
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

// ---------------------------------------------------------------- 状态

const root = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
const search = ref<HTMLInputElement | null>(null)
const panel = ref<HTMLElement | null>(null)

const open = ref(false)
const activeIndex = ref(-1)
/** 向上展开。打开时按视口空间测量一次 */
const dropUp = ref(false)
/** 搜索关键词。关闭即丢弃 */
const query = ref('')

const uid = useId()
const listId = computed(() => `${uid}-list`)
const labelId = computed(() => `${uid}-label`)
const optionId = (index: number) => `${uid}-opt-${index}`

// ---------------------------------------------------------------- 派生

/** 过滤后的候选项。非搜索模式或没有关键词时就是全部 */
const items = computed<SelectOption[]>(() => {
  const needle = query.value.trim().toLowerCase()
  if (!props.searchable || !needle) return props.options
  return props.options.filter((option) => option.label.toLowerCase().includes(needle))
})

const selectedLabel = computed(
  () => props.options.find((option) => option.value === props.modelValue)?.label ?? '',
)

/** 选中项在**当前可见列表**中的位置；被过滤掉时为 -1 */
const visibleSelectedIndex = computed(() =>
  items.value.findIndex((option) => option.value === props.modelValue),
)

/**
 * 打开时输入框里的灰字提示。
 *
 * 它承载的是"当前选中项"，不是"请选择"，所以样式上要跟正文同色（见
 * `.select__search--has-selection`）—— 否则点开的一瞬间文字会由亮变暗。
 */
const searchPlaceholder = computed(() => selectedLabel.value || props.placeholder)

const activeDescendant = computed(() =>
  open.value && activeIndex.value >= 0 ? optionId(activeIndex.value) : undefined,
)

/** 无障碍名称：优先来自可见标签，没有标签时才用 ariaLabel */
const nameFrom = computed(() => (props.label ? labelId.value : undefined))
const nameText = computed(() => (props.label ? undefined : props.ariaLabel))

// ---------------------------------------------------------------- 索引

/**
 * 列表里第一个可用项的位置。`delta` 为负时从末尾往前找。
 *
 * 列表显式传进来而不是闭包捕获 `items`：调用方有时要对"即将生效的列表"求值，
 * 隐式捕获会让那种调用看起来像在读写同一份数据。
 */
function firstEnabled(list: SelectOption[], delta = 1): number {
  for (let step = 0; step < list.length; step += 1) {
    const index = delta > 0 ? step : list.length - 1 - step
    if (!list[index]?.disabled) return index
  }
  return -1
}

/**
 * **安全网**：活动项必须始终指向一个可用项，否则键盘会停在一个不存在的位置上。
 *
 * 这里只做"修正"不做"重置" —— 不能一有变化就跳回第一项，因为父组件每次渲染都
 * 可能传来新的 options 数组，那样键盘导航会被反复打断。
 * "打完字从第一项开始"是**意图**，由 `onSearchInput` 自己表达。
 */
watch(items, (list) => {
  const current = list[activeIndex.value]
  if (current && !current.disabled) return
  activeIndex.value = firstEnabled(list, 1)
})

watch(
  () => props.disabled,
  (disabled) => {
    if (disabled) closeMenu(false)
  },
)

// ---------------------------------------------------------------- 开合

/**
 * 打开面板。
 *
 * - `preferLast`：`↑` 从关闭状态打开时落到最末可用项，与原生一致
 * - `seed`：关闭状态下直接打字时，把那一下按键带进搜索词，否则用户得先按
 *   Enter 打开、再重新打一遍
 */
async function openMenu({ preferLast = false, seed = '' } = {}): Promise<void> {
  if (props.disabled || open.value) return
  open.value = true
  query.value = seed

  // 没有搜索词时停在选中项上；有搜索词说明用户要重新找，从第一项开始
  activeIndex.value =
    visibleSelectedIndex.value >= 0 && !seed
      ? visibleSelectedIndex.value
      : firstEnabled(items.value, preferLast ? -1 : 1)

  await nextTick()
  measureDirection()
  scrollActiveIntoView()
  // 打开后控件已经是输入框（搜索模式），等 nextTick 就是为了拿到它。
  // 从尖角打开时控件本来没有焦点，不显式给的话键盘导航会失灵。
  if (props.searchable) search.value?.focus()
  else trigger.value?.focus()
}

function closeMenu(restoreFocus = true): void {
  if (!open.value) return
  open.value = false
  dropUp.value = false
  query.value = ''
  typed = ''

  // 焦点必须**等重新渲染之后**再放：搜索模式下这一步会把输入框换成按钮，
  // 立刻 focus 会落到那个马上要被卸载的输入框上。
  if (restoreFocus) void nextTick(() => trigger.value?.focus())
}

/**
 * 下方空间不够就向上展开。
 *
 * 只在向上确实比向下宽敞时才翻，否则宁可向下并让列表内部滚动 ——
 * 触发器贴着视口底部时，向上翻也救不了多少。
 */
function measureDirection(): void {
  const control = search.value ?? trigger.value
  const panelEl = panel.value
  if (!control || !panelEl) return

  const rect = control.getBoundingClientRect()
  const spaceBelow = window.innerHeight - rect.bottom
  const needed = panelEl.offsetHeight + 8
  dropUp.value = spaceBelow < needed && rect.top > spaceBelow
}

function scrollActiveIntoView(): void {
  const el = panel.value?.querySelector<HTMLElement>('[data-active="true"]')
  // happy-dom 等环境可能没有实现；缺了只是不滚动，不该让组件报错
  el?.scrollIntoView?.({ block: 'nearest' })
}

// ---------------------------------------------------------------- 选择

/** 按方向移到下一个可用项，跳过禁用项并环绕。 */
function move(delta: number): void {
  const list = items.value
  if (list.length === 0) return

  if (activeIndex.value < 0) {
    activeIndex.value = firstEnabled(list, delta)
    void nextTick(scrollActiveIntoView)
    return
  }

  let index = activeIndex.value
  for (let step = 0; step < list.length; step += 1) {
    index = (index + delta + list.length) % list.length
    if (!list[index]?.disabled) {
      activeIndex.value = index
      void nextTick(scrollActiveIntoView)
      return
    }
  }
}

/**
 * 选中一项。
 *
 * **鼠标与键盘走同一条路径**，因为焦点交给的是"关闭后的那个按钮"，而按钮不显示
 * 文本光标 —— 既不会看起来像还在编辑，键盘用户选完接着 Tab 也不会丢位置。
 */
function choose(option: SelectOption): void {
  if (option.disabled) return
  if (option.value !== props.modelValue) emit('update:modelValue', option.value)
  closeMenu(true)
}

function commitActive(): void {
  const option = items.value[activeIndex.value]
  if (option) choose(option)
}

function hover(index: number, option: SelectOption): void {
  if (option.disabled) return
  activeIndex.value = index
}

// ---------------------------------------------------------------- 首字母跳转

/** 连打字母的缓冲。连按同一个字母表示轮换，而不是攒成 "aa"（那匹配不到东西）。 */
let typed = ''
let typedTimer: ReturnType<typeof setTimeout> | undefined

function typeAhead(char: string): void {
  const lower = char.toLowerCase()
  const cycling = typed.length > 0 && [...typed].every((item) => item === lower)
  typed = cycling ? lower : typed + lower

  clearTimeout(typedTimer)
  typedTimer = setTimeout(() => {
    typed = ''
  }, 500)

  const list = items.value
  if (list.length === 0) return

  // 搜索从列表开头开始；"打首字母"的预期是找到它，不是找当前位置之后的下一个。
  // 轮换时才从当前项之后继续。
  const start = cycling ? activeIndex.value + 1 : 0
  for (let step = 0; step < list.length; step += 1) {
    const index = (start + step + list.length) % list.length
    const option = list[index]
    if (option && !option.disabled && option.label.toLowerCase().startsWith(typed)) {
      if (!open.value) void openMenu()
      activeIndex.value = index
      void nextTick(scrollActiveIntoView)
      return
    }
  }
}

// ---------------------------------------------------------------- 事件

function onSearchInput(event: Event): void {
  query.value = (event.target as HTMLInputElement).value
  // 表达**意图**：改过关键词就从第一项重新开始，而不是让高亮留在原来的位置上
  // （那个位置在新列表里已经没有意义了）。安全网是上面那个 watch。
  activeIndex.value = firstEnabled(items.value, 1)
}

/**
 * 焦点离开整个组件时关闭。
 *
 * **`relatedTarget` 为 null 时不关。** 那不是"焦点去了别处"，而是"焦点哪儿也没去"
 * —— 点了不可聚焦的元素、或整个窗口失焦。点面板内部恰恰走这条路：`<li>` 不可
 * 聚焦，`mousedown` 的默认动作会让控件失焦、`relatedTarget` 为 null，若此时关掉
 * 菜单，等着被点的那个选项就从 DOM 里消失了，`click` 再也落不到它上面 ——
 * 表现就是"鼠标点了没反应，但回车可以"。
 *
 * 点外部由 document 上的 `mousedown` 兜住（它比 `focusout` 更早触发），所以这里
 * 放宽不会漏掉真正的"点到外面去了"。
 */
function onFocusOut(event: FocusEvent): void {
  const next = event.relatedTarget as Node | null
  if (!next) return
  if (root.value?.contains(next)) return
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
      else void openMenu({ preferLast: delta === -1 })
      return
    }
    case 'Enter':
      event.preventDefault()
      if (open.value) commitActive()
      else void openMenu()
      return
    case ' ':
      // 输入框里空格是普通字符，不能当成"确认"；按钮上空格才是激活
      if ((event.target as HTMLElement | null)?.tagName === 'INPUT') return
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
        activeIndex.value = firstEnabled(items.value, 1)
        void nextTick(scrollActiveIntoView)
      }
      return
    case 'End':
      if (open.value) {
        event.preventDefault()
        activeIndex.value = firstEnabled(items.value, -1)
        void nextTick(scrollActiveIntoView)
      }
      return
    case 'Tab':
      // 关闭但**不抢回焦点** —— 用户明确要走了
      closeMenu(false)
      return
    default: {
      const printable =
        event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey
      if (!printable) return

      if (props.searchable) {
        // 关闭时控件是按钮，直接打字说明想搜索
        if (!open.value) void openMenu({ seed: event.key })
        return
      }
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

function toggle(): void {
  if (open.value) closeMenu()
  else void openMenu()
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
    它会把点击转发给内部的控件，点选项时等于把按钮又点了一次，菜单会被重新
    打开）。无障碍名称走 aria-labelledby。
    ref 必须挂在这个根节点上，否则"点外部关闭"判断不到自身。
  -->
  <div ref="root" :class="label ? 'field' : 'select-root'" @focusout="onFocusOut">
    <span v-if="label" :id="labelId" class="field__label">{{ label }}</span>

    <div class="select" :class="{ 'select--open': open, 'select--disabled': disabled }">
      <!--
        控件的形态取决于**此刻是否可编辑**：
          - 关闭时永远是按钮，只负责展示当前选中项 —— 按钮不显示文本光标，
            所以选完之后不会看起来像还在编辑
          - 打开且可搜索时才换成输入框

        两个元素的外观差异（高度、文字颜色）都在样式里对齐了，否则切换时
        控件会闪一下。
      -->
      <input
        v-if="searchable && open"
        ref="search"
        type="text"
        class="input select__trigger select__search"
        :class="{ 'select__search--has-selection': !!selectedLabel }"
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
        :value="query"
        :placeholder="searchPlaceholder"
        @input="onSearchInput"
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
        @click="toggle"
        @keydown="onTriggerKeydown"
      >
        <span class="select__value" :class="{ 'select__value--empty': !selectedLabel }">
          {{ selectedLabel || placeholder }}
        </span>
      </button>

      <span class="select__caret" aria-hidden="true" @mousedown.prevent @click="toggle" />

      <!-- 列表与"无匹配"共用面板样式，两者互斥渲染 -->
      <ul
        v-if="open && items.length > 0"
        :id="listId"
        ref="panel"
        class="select__panel select__list"
        :class="{ 'select__panel--up': dropUp }"
        role="listbox"
        :aria-labelledby="nameFrom"
        :aria-label="nameText"
        @mousedown.prevent
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
      </ul>

      <!--
        空结果放在 listbox **外面**：listbox 里只该有 option，塞一句提示进去是
        无效结构。放外面并加 aria-live，读屏才会念出"没有匹配的选项"。
      -->
      <p
        v-else-if="open"
        ref="panel"
        class="select__panel select__empty"
        :class="{ 'select__panel--up': dropUp }"
        aria-live="polite"
      >
        没有匹配的选项
      </p>
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

/*
  外观复用 .input（共享样式里与 input/textarea 同一套），这里补三件事：

  1. 给尖角让位，否则长文本会钻到它底下
  2. **统一最小高度**。`<button>` 按内容行盒算高、`<input>` 按字体度量算高，
     两者差 1–3px；控件在两者之间切换时，整个控件连同下方内容都会位移一下。
     用同一个最小高度把这件事钉死：1.5em 行高 + 上下 padding 20px + 上下边框 2px。
  3. 左对齐（按钮默认居中）
*/
.select__trigger {
  min-height: calc(1.5em + 22px);
  padding-right: 34px;
  text-align: left;
  cursor: pointer;
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

/*
  打开时输入框的灰字提示承载的是"当前选中项"，不是"请选择"。
  用正文色而不是占位色 —— 否则点开的一瞬间那行字会由亮变暗，看起来像闪了一下。
*/
.select__search--has-selection::placeholder {
  color: var(--bone);
}

/*
  打开期间边框就该是红的，不依赖 `:focus`。
  切换元素的那一帧焦点是断的（按钮已卸载、输入框还没聚焦），只靠 `:focus`
  会先按灰边框渲染再过渡到红，变成一次可见的渐变。
*/
.select--open .select__trigger {
  border-color: var(--red-hi);
}

.select__value {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 占位态用更暗的颜色，与 input 的 ::placeholder 对齐 */
.select__value--empty {
  color: var(--dim);
}

/*
  用两条边转出一个尖角，而不是引图标字体或 SVG。

  位置只由 `top` + `margin-top` 决定且**不随开合变化**，开合只改 `transform`：
  早先版本在打开时把 margin-top 从 -5px 改成 -2px，位移没有过渡、旋转有过渡，
  于是尖角一边平滑旋转一边瞬移 3px，看着像抖了一下。
*/
.select__caret {
  position: absolute;
  top: 50%;
  right: 12px;
  width: 7px;
  height: 7px;
  margin-top: -3.5px;
  border-right: 1.5px solid var(--mute);
  border-bottom: 1.5px solid var(--mute);
  transform: translateY(-2px) rotate(45deg);
  transition: transform var(--transition-fast);
  cursor: pointer;
}

.select--open .select__caret {
  transform: translateY(2px) rotate(-135deg);
  border-color: var(--red-hi);
}

/* 列表与空结果共用的面板外观 */
.select__panel {
  position: absolute;
  z-index: 20;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  background: var(--panel);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-surface);
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.45);
}

.select__panel--up {
  top: auto;
  bottom: calc(100% + 4px);
}

.select__list {
  margin: 0;
  padding: 4px;
  list-style: none;
  /* 超过这个高度就在列表内部滚动，而不是把页面撑长 */
  max-height: 260px;
  overflow-y: auto;
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
  margin: 0;
  padding: 12px 10px;
  font-size: 12.5px;
  color: var(--dim);
  text-align: center;
}
</style>
