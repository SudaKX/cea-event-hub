/**
 * 自定义下拉的交互契约。
 *
 * 这个组件存在的唯一理由是"展开列表要能上色"，而它为此接手了原生 `<select>`
 * 白送的全部职责。所以测试重点不在渲染，而在**那些被接手的职责**：键盘导航、
 * ARIA 接线、点外部关闭、跳过禁用项。少一样就是一次可访问性回归。
 */
import { mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import Select, { type SelectOption } from './Select.vue'

const OPTIONS: SelectOption[] = [
  { value: '', label: '全部' },
  { value: 'received', label: 'received' },
  { value: 'reviewing', label: 'reviewing' },
  { value: 'accepted', label: 'accepted' },
]

function make(props: Record<string, unknown> = {}) {
  return mount(Select, {
    props: { modelValue: '', options: OPTIONS, label: '状态', ...props },
    attachTo: document.body,
  })
}

/** 打开菜单并等 openMenu 内部的 nextTick 落地。两种模式的触发方式不同 */
async function openMenu(wrapper: ReturnType<typeof make>) {
  const search = wrapper.find('input.select__search')
  if (search.exists()) await search.trigger('focus')
  else await wrapper.find('button.select__trigger').trigger('click')
  await wrapper.vm.$nextTick()
  await wrapper.vm.$nextTick()
}

describe('渲染', () => {
  it('未选中时显示占位文案', () => {
    const wrapper = make({ modelValue: '', options: [{ value: '', label: '全部' }] })
    // 选中项的 label 是"全部"，所以这里显式给一个不匹配的值来走占位分支
    const other = make({ modelValue: 'nope' })
    expect(other.find('.select__value').text()).toBe('请选择')
    expect(other.find('.select__value--empty').exists()).toBe(true)
    wrapper.unmount()
    other.unmount()
  })

  it('显示选中项的标签而不是值', () => {
    const wrapper = make({ modelValue: 'accepted' })
    expect(wrapper.find('.select__value').text()).toBe('accepted')
    expect(wrapper.find('.select__value--empty').exists()).toBe(false)
    wrapper.unmount()
  })

  it('带 label 时渲染出 field 结构', () => {
    const wrapper = make()
    // 刻意是 div.field 而不是 label.field —— 后者会把点击转发给内部按钮，
    // 点选项时等于把按钮又点了一次（见"点选项即选中并关闭"）
    expect(wrapper.find('div.field').exists()).toBe(true)
    expect(wrapper.find('label.field').exists()).toBe(false)
    expect(wrapper.find('.field__label').text()).toBe('状态')
    wrapper.unmount()
  })

  it('不带 label 时不渲染 field 结构', () => {
    const wrapper = make({ label: undefined, ariaLabel: '状态' })
    expect(wrapper.find('label.field').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('ARIA 接线', () => {
  it('触发器是 combobox，并正确反映展开状态', async () => {
    const wrapper = make({ modelValue: 'accepted' })
    const trigger = wrapper.find('.select__trigger')

    expect(trigger.attributes('role')).toBe('combobox')
    expect(trigger.attributes('aria-haspopup')).toBe('listbox')
    expect(trigger.attributes('aria-expanded')).toBe('false')

    await openMenu(wrapper)
    expect(wrapper.find('.select__trigger').attributes('aria-expanded')).toBe('true')
    wrapper.unmount()
  })

  it('选项有 option 角色且标出选中项', async () => {
    const wrapper = make({ modelValue: 'accepted' })
    await openMenu(wrapper)

    const options = wrapper.findAll('[role="option"]')
    expect(options).toHaveLength(OPTIONS.length)
    expect(options[3]!.attributes('aria-selected')).toBe('true')
    expect(options[1]!.attributes('aria-selected')).toBe('false')
    wrapper.unmount()
  })

  it('用 aria-activedescendant 指向当前项，焦点本身留在按钮上', async () => {
    // select-only combobox 的标准做法：不把焦点移进列表
    const wrapper = make({ modelValue: 'accepted' })
    await openMenu(wrapper)

    const trigger = wrapper.find('.select__trigger')
    const active = trigger.attributes('aria-activedescendant')
    expect(active).toBeTruthy()
    expect(wrapper.find(`#${active}`).text()).toBe('accepted')
    wrapper.unmount()
  })

  it('用可见标签作为无障碍名称', () => {
    const wrapper = make()
    const trigger = wrapper.find('.select__trigger')
    expect(trigger.attributes('aria-labelledby')).toBeTruthy()
    expect(wrapper.find(`#${trigger.attributes('aria-labelledby')}`).text()).toBe('状态')
    wrapper.unmount()
  })

  it('没有可见标签时退回 ariaLabel', () => {
    const wrapper = make({ label: undefined, ariaLabel: '筛选项' })
    expect(wrapper.find('.select__trigger').attributes('aria-label')).toBe('筛选项')
    wrapper.unmount()
  })
})

describe('鼠标', () => {
  it('点选项即选中并关闭', async () => {
    const wrapper = make()
    await openMenu(wrapper)

    await wrapper.findAll('[role="option"]')[2]!.trigger('click')
    expect(wrapper.emitted('update:modelValue')).toEqual([['reviewing']])
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('重复选中同一项不重复派发', async () => {
    const wrapper = make({ modelValue: 'reviewing' })
    await openMenu(wrapper)

    await wrapper.findAll('[role="option"]')[2]!.trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    // 但仍然关闭
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('点外部关闭', async () => {
    const wrapper = make()
    await openMenu(wrapper)
    expect(wrapper.find('[role="listbox"]').exists()).toBe(true)

    document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('在组件内部按下不关闭', async () => {
    const wrapper = make()
    await openMenu(wrapper)

    await wrapper.find('.select__trigger').trigger('mousedown')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[role="listbox"]').exists()).toBe(true)
    wrapper.unmount()
  })
})

describe('键盘', () => {
  it('↓ 打开并落到当前选中项', async () => {
    const wrapper = make({ modelValue: 'reviewing' })
    await wrapper.find('.select__trigger').trigger('keydown', { key: 'ArrowDown' })
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[role="listbox"]').exists()).toBe(true)
    const active = wrapper.find('.select__trigger').attributes('aria-activedescendant')
    expect(wrapper.find(`#${active}`).text()).toBe('reviewing')
    wrapper.unmount()
  })

  it('关闭状态下 ↑ 打开并落到最后一项', async () => {
    // 用一个不匹配任何选项的值，才能走到"未选中 -> 落到末尾"这条分支；
    // 有选中值时 ↑ 与原生一致，打开就停在选中项上
    const wrapper = make({ modelValue: 'no-such-value' })
    await wrapper.find('.select__trigger').trigger('keydown', { key: 'ArrowUp' })
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    const active = wrapper.find('.select__trigger').attributes('aria-activedescendant')
    expect(wrapper.find(`#${active}`).text()).toBe('accepted')
    wrapper.unmount()
  })

  it('有选中值时 ↑ 停在选中项而不是末尾', async () => {
    const wrapper = make({ modelValue: 'reviewing' })
    await wrapper.find('.select__trigger').trigger('keydown', { key: 'ArrowUp' })
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    const active = wrapper.find('.select__trigger').attributes('aria-activedescendant')
    expect(wrapper.find(`#${active}`).text()).toBe('reviewing')
    wrapper.unmount()
  })

  it('↓/↑ 移动并在两端环绕', async () => {
    const wrapper = make({ modelValue: 'accepted' })
    await openMenu(wrapper)

    const activeText = () => {
      const id = wrapper.find('.select__trigger').attributes('aria-activedescendant')
      return wrapper.find(`#${id}`).text()
    }

    await wrapper.find('.select__trigger').trigger('keydown', { key: 'ArrowDown' })
    await wrapper.vm.$nextTick()
    expect(activeText()).toBe('全部') // 从末项绕回首项

    await wrapper.find('.select__trigger').trigger('keydown', { key: 'ArrowUp' })
    await wrapper.vm.$nextTick()
    expect(activeText()).toBe('accepted') // 再绕回末项
    wrapper.unmount()
  })

  it('Home / End 跳到首尾', async () => {
    const wrapper = make({ modelValue: 'reviewing' })
    await openMenu(wrapper)

    const activeText = () => {
      const id = wrapper.find('.select__trigger').attributes('aria-activedescendant')
      return wrapper.find(`#${id}`).text()
    }

    await wrapper.find('.select__trigger').trigger('keydown', { key: 'End' })
    await wrapper.vm.$nextTick()
    expect(activeText()).toBe('accepted')

    await wrapper.find('.select__trigger').trigger('keydown', { key: 'Home' })
    await wrapper.vm.$nextTick()
    expect(activeText()).toBe('全部')
    wrapper.unmount()
  })

  it('Enter 选中当前项', async () => {
    const wrapper = make()
    await openMenu(wrapper)

    await wrapper.find('.select__trigger').trigger('keydown', { key: 'ArrowDown' })
    await wrapper.vm.$nextTick()
    await wrapper.find('.select__trigger').trigger('keydown', { key: 'Enter' })

    expect(wrapper.emitted('update:modelValue')).toEqual([['received']])
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('Space 也能选中', async () => {
    const wrapper = make()
    await openMenu(wrapper)
    await wrapper.find('.select__trigger').trigger('keydown', { key: 'ArrowDown' })
    await wrapper.vm.$nextTick()
    await wrapper.find('.select__trigger').trigger('keydown', { key: ' ' })

    expect(wrapper.emitted('update:modelValue')).toEqual([['received']])
    wrapper.unmount()
  })

  it('Esc 关闭但不改变选择', async () => {
    const wrapper = make({ modelValue: 'reviewing' })
    await openMenu(wrapper)

    await wrapper.find('.select__trigger').trigger('keydown', { key: 'ArrowDown' })
    await wrapper.vm.$nextTick()
    await wrapper.find('.select__trigger').trigger('keydown', { key: 'Escape' })
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[role="listbox"]').exists()).toBe(false)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    wrapper.unmount()
  })

  it('Tab 关闭且不抢回焦点', async () => {
    const wrapper = make()
    await openMenu(wrapper)

    // 先把焦点放到组件外面，才能验"没有被抢回来"
    const outside = document.createElement('input')
    document.body.appendChild(outside)
    outside.focus()

    await wrapper.find('.select__trigger').trigger('keydown', { key: 'Tab' })
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[role="listbox"]').exists()).toBe(false)
    // Tab 是"我要走了"，此时把焦点抢回控件会打断键盘用户
    expect(document.activeElement).toBe(outside)
    outside.remove()
    wrapper.unmount()
  })

  it('首字母跳转从列表开头找第一项', async () => {
    // 打开后活动项停在"全部"(index 0)；打 'a' 应当落到第一项以 a 开头的
    // （"accepted"），而不是"当前项之后的第一个匹配"
    const wrapper = make()
    await openMenu(wrapper)

    await wrapper.find('.select__trigger').trigger('keydown', { key: 'a' })
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    const active = wrapper.find('.select__trigger').attributes('aria-activedescendant')
    expect(wrapper.find(`#${active}`).text()).toBe('accepted')
    wrapper.unmount()
  })

  it('连打字母按前缀收窄', async () => {
    const wrapper = make({
      options: [
        { value: 'a', label: 'apple' },
        { value: 'b', label: 'apricot' },
        { value: 'c', label: 'banana' },
      ],
    })
    await openMenu(wrapper)

    const trigger = wrapper.find('.select__trigger')
    const activeText = () => {
      const id = trigger.attributes('aria-activedescendant')
      return wrapper.find(`#${id}`).text()
    }

    await trigger.trigger('keydown', { key: 'a' })
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    expect(activeText()).toBe('apple')

    // 'pr' 把两个 a 开头的都排除
    await trigger.trigger('keydown', { key: 'p' })
    await wrapper.vm.$nextTick()
    await trigger.trigger('keydown', { key: 'r' })
    await wrapper.vm.$nextTick()
    expect(activeText()).toBe('apricot')
    wrapper.unmount()
  })

  it('连按同一个字母在同首字母的项之间轮换', async () => {
    const wrapper = make({
      options: [
        { value: 'a', label: 'apple' },
        { value: 'b', label: 'apricot' },
        { value: 'c', label: 'banana' },
      ],
    })
    await openMenu(wrapper)

    const trigger = wrapper.find('.select__trigger')
    const activeText = () => {
      const id = trigger.attributes('aria-activedescendant')
      return wrapper.find(`#${id}`).text()
    }

    await trigger.trigger('keydown', { key: 'a' })
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    expect(activeText()).toBe('apple')

    // 再按一次 'a' 不该变成找 "aa"，而是在 apple/apricot 之间轮换
    await trigger.trigger('keydown', { key: 'a' })
    await wrapper.vm.$nextTick()
    expect(activeText()).toBe('apricot')

    await trigger.trigger('keydown', { key: 'a' })
    await wrapper.vm.$nextTick()
    expect(activeText()).toBe('apple')
    wrapper.unmount()
  })

  it('组合键不触发首字母跳转', async () => {
    const wrapper = make()
    await openMenu(wrapper)

    await wrapper
      .find('.select__trigger')
      .trigger('keydown', { key: 'a', ctrlKey: true })
    await wrapper.vm.$nextTick()

    // 停在打开时的位置（"全部"），没有跳走
    const active = wrapper.find('.select__trigger').attributes('aria-activedescendant')
    expect(wrapper.find(`#${active}`).text()).toBe('全部')
    wrapper.unmount()
  })
})

describe('禁用项', () => {
  const withDisabled: SelectOption[] = [
    { value: 'a', label: 'a' },
    { value: 'b', label: 'b', disabled: true },
    { value: 'c', label: 'c' },
  ]

  it('键盘跳过禁用项', async () => {
    const wrapper = make({ options: withDisabled })
    await openMenu(wrapper)

    const trigger = wrapper.find('.select__trigger')
    await trigger.trigger('keydown', { key: 'ArrowDown' })
    await wrapper.vm.$nextTick()

    const id = trigger.attributes('aria-activedescendant')
    expect(wrapper.find(`#${id}`).text()).toBe('c') // 跳过 b
    wrapper.unmount()
  })

  it('点击禁用项没有反应', async () => {
    const wrapper = make({ options: withDisabled })
    await openMenu(wrapper)

    await wrapper.findAll('[role="option"]')[1]!.trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    // 菜单保持打开：点了没用的东西，不该顺手把菜单也关掉
    expect(wrapper.find('[role="listbox"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('标出 aria-disabled', async () => {
    const wrapper = make({ options: withDisabled })
    await openMenu(wrapper)
    expect(
      wrapper.findAll('[role="option"]')[1]!.attributes('aria-disabled'),
    ).toBe('true')
    wrapper.unmount()
  })
})

describe('整体禁用', () => {
  it('触发器禁用且点不开', async () => {
    const wrapper = make({ disabled: true })
    const trigger = wrapper.find('.select__trigger')

    expect(trigger.attributes('disabled')).toBeDefined()
    await trigger.trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false)
    wrapper.unmount()
  })
})

/* ------------------------------------------------------------------ */
/* 失焦与鼠标点击的交互                                                */
/* ------------------------------------------------------------------ */

/**
 * 这一组是回归测试，对应一个真实缺陷：**鼠标点选项没反应，但回车可以**。
 *
 * 原因是 `<li>` 不可聚焦，`mousedown` 的默认动作会让控件失焦，`focusout` 随之
 * 以 `relatedTarget = null` 触发；当时的处理会因此关掉菜单，于是**等着被点的
 * 那个选项在 `click` 派发之前就从 DOM 里消失了**。回车不受影响，因为焦点全程
 * 没离开控件。
 *
 * 原有测试没能发现它，是因为它们只用 `trigger('click')` —— happy-dom 不会因为
 * `mousedown` 而失焦，真实的失焦序列从未被模拟过。所以下面显式模拟它。
 */
describe('失焦与鼠标点击', () => {
  for (const searchable of [false, true]) {
    const mode = searchable ? '搜索模式' : '按钮模式'

    it(`${mode}：控件失焦到 null 时不关闭，选项仍在 DOM 里可点`, async () => {
      const wrapper = searchable ? makeSearchable() : make()
      await openMenu(wrapper)
      expect(wrapper.find('[role="option"]').exists()).toBe(true)

      // mousedown 的默认动作：控件失焦，而 <li> 不可聚焦所以 relatedTarget 是 null
      const control = wrapper.find('.select__trigger')
      await control.trigger('focusout', { relatedTarget: null })
      await wrapper.vm.$nextTick()

      // 浏览器此刻才要派发 click —— 它必须还有东西可点
      expect(wrapper.find('[role="option"]').exists()).toBe(true)
      expect(wrapper.find('[role="listbox"]').exists()).toBe(true)
      wrapper.unmount()
    })

    it(`${mode}：在选项上按下鼠标不会夺走焦点`, async () => {
      const wrapper = searchable ? makeSearchable() : make()
      await openMenu(wrapper)

      const event = new MouseEvent('mousedown', { bubbles: true, cancelable: true })
      wrapper.findAll('[role="option"]')[0]!.element.dispatchEvent(event)

      // preventDefault 挡住了默认的焦点转移 —— 这是第一道防线
      expect(event.defaultPrevented).toBe(true)
      wrapper.unmount()
    })

    it(`${mode}：焦点移到组件外的真实元素时关闭`, async () => {
      const wrapper = searchable ? makeSearchable() : make()
      await openMenu(wrapper)

      const outside = document.createElement('input')
      document.body.appendChild(outside)

      await wrapper
        .find('.select__trigger')
        .trigger('focusout', { relatedTarget: outside })
      await wrapper.vm.$nextTick()

      expect(wrapper.find('[role="listbox"]').exists()).toBe(false)
      outside.remove()
      wrapper.unmount()
    })

    it(`${mode}：完整的 mousedown -> click 序列能选中`, async () => {
      const wrapper = searchable ? makeSearchable() : make()
      await openMenu(wrapper)

      const option = wrapper.findAll('[role="option"]')[1]!
      await option.trigger('mousedown')
      await option.trigger('click')

      expect(wrapper.emitted('update:modelValue')).toHaveLength(1)
      expect(wrapper.find('[role="listbox"]').exists()).toBe(false)
      wrapper.unmount()
    })
  }

  it('点尖角按钮同样能开合，并把焦点交给控件', async () => {
    const wrapper = make()
    await wrapper.find('.select__caret').trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[role="listbox"]').exists()).toBe(true)
    // 焦点在控件上，键盘导航随之可用；否则点完尖角再按方向键会毫无反应
    expect(document.activeElement).toBe(wrapper.find('.select__trigger').element)
    wrapper.unmount()
  })

  it('尖角在打开时点击则关闭', async () => {
    const wrapper = make()
    await openMenu(wrapper)
    await wrapper.find('.select__caret').trigger('click')
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[role="listbox"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('禁用后关闭已展开的面板', async () => {
    const wrapper = make()
    await openMenu(wrapper)
    expect(wrapper.find('[role="listbox"]').exists()).toBe(true)

    await wrapper.setProps({ disabled: true })
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false)
    wrapper.unmount()
  })
})

/* ------------------------------------------------------------------ */
/* 选中后的展示状态                                                    */
/* ------------------------------------------------------------------ */

/**
 * 这一组需要**真实的 v-model 回写**：只有父组件把新值传回来，"选中后展示什么"
 * 才有意义。`make*` 那几个 helper 用的是静态 props，断言不到这个。
 */
const Host = defineComponent({
  components: { Select },
  props: { options: { type: Array, required: true }, searchable: Boolean },
  setup(props) {
    const picked = ref('')
    return { picked, props }
  },
  template: `<Select v-model="picked" label="活动" :options="options" :searchable="searchable" />`,
})

function makeHost(options: SelectOption[], searchable = false) {
  return mount(Host, { props: { options, searchable }, attachTo: document.body })
}

describe('选中后的展示状态', () => {
  const OPTIONS: SelectOption[] = [
    { value: 'spring-2026', label: 'spring-2026 — 春季招新' },
    { value: 'workshop', label: 'workshop — 嵌入式工作坊' },
    { value: 'hackathon', label: 'hackathon — 黑客松' },
  ]

  it('搜索模式下鼠标选中后失焦，不再是编辑状态', async () => {
    const wrapper = makeHost(OPTIONS, true)
    const input = wrapper.find('input')
    const el = input.element as HTMLInputElement

    await input.trigger('focus')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    await input.setValue('work')
    await wrapper.vm.$nextTick()

    await wrapper.findAll('[role="option"]')[0]!.trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    // 值要对：显示的是新选中的标签，不是刚才输入的关键词
    expect(el.value).toBe('workshop — 嵌入式工作坊')
    // 焦点要交出去：留着就会出现文本光标，看起来像还在编辑
    expect(document.activeElement).not.toBe(el)
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('搜索模式下键盘选中后仍保持聚焦', async () => {
    // 键盘用户选完多半要继续 Tab，焦点被夺走会让 Tab 从 body 重新开始
    const wrapper = makeHost(OPTIONS, true)
    const input = wrapper.find('input')
    const el = input.element as HTMLInputElement

    await input.trigger('focus')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    await input.setValue('hack')
    await wrapper.vm.$nextTick()

    await input.trigger('keydown', { key: 'Enter' })
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    expect(el.value).toBe('hackathon — 黑客松')
    expect(document.activeElement).toBe(el)
    wrapper.unmount()
  })

  it('按钮模式下鼠标选中后显示新标签', async () => {
    const wrapper = makeHost(OPTIONS, false)
    await wrapper.find('button.select__trigger').trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    await wrapper.findAll('[role="option"]')[2]!.trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.select__value').text()).toBe('hackathon — 黑客松')
    expect(wrapper.find('.select__value--empty').exists()).toBe(false)
    wrapper.unmount()
  })

  it('键盘选完后 Tab 走开不会被抢回焦点', async () => {
    const wrapper = makeHost(OPTIONS, true)
    const input = wrapper.find('input')

    await input.trigger('focus')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    await input.setValue('spring')
    await wrapper.vm.$nextTick()
    await input.trigger('keydown', { key: 'Enter' })
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    const outside = document.createElement('input')
    document.body.appendChild(outside)
    outside.focus()
    await input.trigger('keydown', { key: 'Tab' })
    await wrapper.vm.$nextTick()

    expect(document.activeElement).toBe(outside)
    outside.remove()
    wrapper.unmount()
  })
})

describe('卸载', () => {
  it('移除文档级监听，避免残留', async () => {
    const remove = vi.spyOn(document, 'removeEventListener')
    const wrapper = make()
    wrapper.unmount()
    expect(remove).toHaveBeenCalledWith('mousedown', expect.any(Function))
    remove.mockRestore()
  })
})

/* ------------------------------------------------------------------ */
/* 搜索模式                                                            */
/* ------------------------------------------------------------------ */

const EVENTS: SelectOption[] = [
  { value: 'spring-2026', label: 'spring-2026 — 春季招新' },
  { value: 'autumn-2026', label: 'autumn-2026 — 秋季招新' },
  { value: 'workshop', label: 'workshop — 嵌入式工作坊' },
  { value: 'hackathon', label: 'hackathon — 黑客松' },
]

function makeSearchable(props: Record<string, unknown> = {}) {
  return mount(Select, {
    props: {
      modelValue: '',
      options: EVENTS,
      label: '活动',
      searchable: true,
      ...props,
    },
    attachTo: document.body,
  })
}

const labelsOf = (wrapper: ReturnType<typeof makeSearchable>) =>
  wrapper.findAll('[role="option"]').map((node) => node.text())

describe('搜索模式', () => {
  it('触发器是输入框而不是按钮，并且是 combobox', () => {
    const wrapper = makeSearchable()
    const input = wrapper.find('input.select__search')

    expect(input.exists()).toBe(true)
    expect(wrapper.find('button.select__trigger').exists()).toBe(false)
    expect(input.attributes('role')).toBe('combobox')
    expect(input.attributes('aria-autocomplete')).toBe('list')
    wrapper.unmount()
  })

  it('未打开时输入框显示当前选中项的标签', () => {
    const wrapper = makeSearchable({ modelValue: 'workshop' })
    expect((wrapper.find('input').element as HTMLInputElement).value).toBe(
      'workshop — 嵌入式工作坊',
    )
    wrapper.unmount()
  })

  it('聚焦即打开，并清空关键词列出全部', async () => {
    const wrapper = makeSearchable({ modelValue: 'workshop' })
    await wrapper.find('input').trigger('focus')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[role="listbox"]').exists()).toBe(true)
    expect(labelsOf(wrapper)).toHaveLength(EVENTS.length)
    // 清空了，但选中项作为灰字提示仍然看得到
    const input = wrapper.find('input').element as HTMLInputElement
    expect(input.value).toBe('')
    expect(input.placeholder).toBe('workshop — 嵌入式工作坊')
    wrapper.unmount()
  })

  it('输入即按子串过滤，不区分大小写', async () => {
    const wrapper = makeSearchable()
    await wrapper.find('input').trigger('focus')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    await wrapper.find('input').setValue('WORKSHOP')
    await wrapper.vm.$nextTick()
    expect(labelsOf(wrapper)).toEqual(['workshop — 嵌入式工作坊'])
    wrapper.unmount()
  })

  it('匹配的是子串而不是前缀', async () => {
    // "招新" 出现在标签中段，前缀匹配找不到
    const wrapper = makeSearchable()
    await wrapper.find('input').trigger('focus')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    await wrapper.find('input').setValue('招新')
    await wrapper.vm.$nextTick()
    expect(labelsOf(wrapper)).toEqual([
      'spring-2026 — 春季招新',
      'autumn-2026 — 秋季招新',
    ])
    wrapper.unmount()
  })

  it('过滤后活动项落到第一个可用项', async () => {
    const wrapper = makeSearchable()
    await wrapper.find('input').trigger('focus')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    await wrapper.find('input').setValue('hack')
    await wrapper.vm.$nextTick()

    const id = wrapper.find('input').attributes('aria-activedescendant')
    expect(wrapper.find(`#${id}`).text()).toBe('hackathon — 黑客松')
    wrapper.unmount()
  })

  it('搜不到时给一句话而不是空气泡', async () => {
    const wrapper = makeSearchable()
    await wrapper.find('input').trigger('focus')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    await wrapper.find('input').setValue('zzzz')
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('[role="option"]')).toHaveLength(0)
    expect(wrapper.find('.select__empty').text()).toBe('没有匹配的选项')
    wrapper.unmount()
  })

  it('回车选中过滤后的项', async () => {
    const wrapper = makeSearchable()
    await wrapper.find('input').trigger('focus')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    // 注意 "autumn" 不含 "auto"（拼写是 a-u-t-u-m-n），这里用真正的中段子串
    await wrapper.find('input').setValue('umn')
    await wrapper.vm.$nextTick()
    expect(labelsOf(wrapper)).toEqual(['autumn-2026 — 秋季招新'])

    await wrapper.find('input').trigger('keydown', { key: 'Enter' })

    expect(wrapper.emitted('update:modelValue')).toEqual([['autumn-2026']])
    wrapper.unmount()
  })

  it('选中后输入框恢复显示新标签', async () => {
    const wrapper = makeSearchable()
    await wrapper.find('input').trigger('focus')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    await wrapper.find('input').setValue('work')
    await wrapper.vm.$nextTick()
    await wrapper.findAll('[role="option"]')[0]!.trigger('click')
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[role="listbox"]').exists()).toBe(false)
    // props 在测试里不会回写，所以这里仍显示旧的（空）选中项
    expect((wrapper.find('input').element as HTMLInputElement).value).toBe('')
    wrapper.unmount()
  })

  it('关闭后再打开丢掉上次的关键词', async () => {
    const wrapper = makeSearchable()
    await wrapper.find('input').trigger('focus')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    await wrapper.find('input').setValue('hack')
    await wrapper.vm.$nextTick()
    expect(labelsOf(wrapper)).toHaveLength(1)

    await wrapper.find('input').trigger('keydown', { key: 'Escape' })
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false)

    await wrapper.find('input').trigger('focus')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    expect(labelsOf(wrapper)).toHaveLength(EVENTS.length)
    wrapper.unmount()
  })

  it('空格是输入字符，不是确认', async () => {
    const wrapper = makeSearchable()
    await wrapper.find('input').trigger('focus')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    await wrapper.find('input').trigger('keydown', { key: ' ' })
    // 没有选中、也没有关闭 —— 空格该被当成搜索词的一部分
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect(wrapper.find('[role="listbox"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('键盘移动只在过滤结果内环绕', async () => {
    const wrapper = makeSearchable()
    await wrapper.find('input').trigger('focus')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    await wrapper.find('input').setValue('20')
    await wrapper.vm.$nextTick()
    expect(labelsOf(wrapper)).toHaveLength(2)

    const activeText = () => {
      const id = wrapper.find('input').attributes('aria-activedescendant')
      return wrapper.find(`#${id}`).text()
    }

    await wrapper.find('input').trigger('keydown', { key: 'ArrowDown' })
    await wrapper.vm.$nextTick()
    expect(activeText()).toBe('autumn-2026 — 秋季招新')

    // 绕回第一项，而不是跑到被过滤掉的项上
    await wrapper.find('input').trigger('keydown', { key: 'ArrowDown' })
    await wrapper.vm.$nextTick()
    expect(activeText()).toBe('spring-2026 — 春季招新')
    wrapper.unmount()
  })

  it('搜索模式不使用首字母跳转（输入本身就是搜索）', async () => {
    const wrapper = makeSearchable()
    await wrapper.find('input').trigger('focus')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    await wrapper.find('input').trigger('keydown', { key: 'h' })
    await wrapper.vm.$nextTick()
    // 没有过滤、也没有把活动项跳到 h 开头的那项
    expect(labelsOf(wrapper)).toHaveLength(EVENTS.length)
    const id = wrapper.find('input').attributes('aria-activedescendant')
    expect(wrapper.find(`#${id}`).text()).toBe(EVENTS[0]!.label)
    wrapper.unmount()
  })

  it('非搜索模式仍然只渲染按钮', () => {
    const wrapper = make({ searchable: false })
    expect(wrapper.find('input.select__search').exists()).toBe(false)
    expect(wrapper.find('button.select__trigger').exists()).toBe(true)
    wrapper.unmount()
  })
})
