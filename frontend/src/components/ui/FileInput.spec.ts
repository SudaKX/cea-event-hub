/**
 * 文件选择。
 *
 * 原生文件输入的外观由浏览器画，暗色界面上会冒出一个浅色按钮 —— 所以这里把它
 * 藏起来、另画触发器。要确认的是：**语义与键盘没丢**，以及**选同一个文件能再次
 * 触发**（原生输入框不清值就不会再发 change，用户会以为点了没反应）。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import FileInput from './FileInput.vue'

function makeFile(name = 'content.zip', size = 2048): File {
  const file = new File(['x'], name, { type: 'application/zip' })
  // File.size 是只读的，用 defineProperty 造出想要的大小
  Object.defineProperty(file, 'size', { value: size })
  return file
}

function make(props: Record<string, unknown> = {}) {
  return mount(FileInput, {
    props: { modelValue: null, ...props },
    attachTo: document.body,
  })
}

const input = (wrapper: ReturnType<typeof make>) =>
  wrapper.find('input[type="file"]').element as HTMLInputElement

const trigger = (wrapper: ReturnType<typeof make>) =>
  wrapper.findAll('button').find((b) => b.text().includes('选择'))!

describe('语义', () => {
  it('真的有一个 file 输入框', () => {
    // 只是视觉隐藏，不能连键盘可达性一起干掉
    const wrapper = make()
    expect(wrapper.find('input[type="file"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('accept 透传给原生输入框', () => {
    const wrapper = make({ accept: '.zip' })
    expect(input(wrapper).accept).toBe('.zip')
    wrapper.unmount()
  })

  it('触发器用的是与输入框同高的按钮档', () => {
    // 与旁边 43px 的输入框并排，矮一截会很明显
    const wrapper = make()
    expect(trigger(wrapper).classes()).toContain('btn--control')
    wrapper.unmount()
  })
})

describe('选择', () => {
  it('点触发器转交给隐藏的输入框', async () => {
    // 不用 <label> 包住：那样键盘焦点会落在看不见的输入框上，焦点样式画不出来
    const wrapper = make()
    const click = vi.spyOn(input(wrapper), 'click')

    await trigger(wrapper).trigger('click')
    expect(click).toHaveBeenCalled()
    wrapper.unmount()
  })

  it('选中后把文件抛上去', async () => {
    const wrapper = make()
    const file = makeFile()

    Object.defineProperty(input(wrapper), 'files', { value: [file], configurable: true })
    await wrapper.find('input').trigger('change')

    expect(wrapper.emitted('update:modelValue')).toEqual([[file]])
    wrapper.unmount()
  })

  it('选完之后清掉原生输入框的值', async () => {
    /*
      不清的话，再选**同一个文件**不会触发 change —— 用户会以为点了没反应。
      这是原生 file 输入最经典的一个坑。

      **行为本身在这个环境里测不了**：规范规定 file 输入的 value 只能被程序设为
      空串，happy-dom 照做（赋值直接抛错），所以造不出"已选中某个文件"这个状态，
      也就无从断言它被清掉。这里退而求其次守住那行代码的存在 —— 变异测试证明它
      确实会在被删掉时失败。真浏览器里的行为只能靠人工确认。
    */
    const source = readFileSync(
      resolve(process.cwd(), 'src/components/ui/FileInput.vue'),
      'utf-8',
    )
    expect(source).toMatch(/target\.value\s*=\s*''/)

    // 顺带确认它确实挂在 change 处理里，而不是别处的一句死代码
    const handler = /function onChange\([\s\S]*?\n\}/.exec(source)?.[0] ?? ''
    expect(handler).toMatch(/target\.value\s*=\s*''/)
  })

  it('取消选择时抛 null', async () => {
    const wrapper = make({ modelValue: makeFile() })
    Object.defineProperty(input(wrapper), 'files', { value: [], configurable: true })

    await wrapper.find('input').trigger('change')
    expect(wrapper.emitted('update:modelValue')).toEqual([[null]])
    wrapper.unmount()
  })
})

describe('已选文件', () => {
  it('显示文件名', () => {
    const wrapper = make({ modelValue: makeFile('spring-2026.zip') })
    expect(wrapper.find('.file__name').text()).toBe('spring-2026.zip')
    wrapper.unmount()
  })

  it('按量级显示体积', () => {
    const cases: [number, string][] = [
      [512, '512 B'],
      [2048, '2.0 KB'],
      [3 * 1024 * 1024, '3.0 MB'],
    ]
    for (const [size, expected] of cases) {
      const wrapper = make({ modelValue: makeFile('a.zip', size) })
      expect(wrapper.find('.file__size').text()).toBe(expected)
      wrapper.unmount()
    }
  })

  it('没选时明说，而不是留空', () => {
    const wrapper = make()
    expect(wrapper.find('.file__empty').text()).toBe('未选择文件')
    expect(wrapper.find('.file__name').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('禁用', () => {
  it('输入框与触发器都被禁用', () => {
    const wrapper = make({ disabled: true })
    expect(input(wrapper).disabled).toBe(true)
    expect(trigger(wrapper).attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('禁用时点触发器不打开对话框', async () => {
    const wrapper = make({ disabled: true })
    const click = vi.spyOn(input(wrapper), 'click')

    await trigger(wrapper).trigger('click')
    expect(click).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
