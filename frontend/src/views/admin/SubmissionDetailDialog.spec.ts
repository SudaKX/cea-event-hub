/**
 * 提交详情对话框。
 *
 * 用原生 `<dialog>` + `showModal()`，所以这里既验证内容渲染，也验证"打开与关闭"
 * 这条容易做错的链路：`showModal` 必须在元素已挂载后调用，而 `@close` 要能覆盖
 * Esc 与 close() 两条路径。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import SubmissionDetailDialog from './SubmissionDetailDialog.vue'
import { SUBMISSION_STATUS } from '@/domain/submission'
import type { Submission } from '@/types/api'

function submission(overrides: Partial<Submission> = {}): Submission {
  return {
    id: 42,
    event_id: 'spring-2026',
    kind: 'signup',
    status: SUBMISSION_STATUS.RECEIVED,
    submitter: 'a:browser-x',
    from_authenticated_user: false,
    payload: { name: '张三', grade: '2' },
    created_at: '2026-10-01T12:00:00Z',
    files: [],
    ...overrides,
  }
}

function make(props: Record<string, unknown> = {}) {
  return mount(SubmissionDetailDialog, {
    props: { submission: null, ...props },
    attachTo: document.body,
  })
}

const dialogEl = (wrapper: ReturnType<typeof make>) =>
  wrapper.find('dialog').element as HTMLDialogElement

describe('开合', () => {
  it('初始关闭，且对话框始终在 DOM 里', () => {
    // showModal() 需要一个已挂载的元素，所以不能用 v-if 把它整块摘掉
    const wrapper = make()
    expect(wrapper.find('dialog').exists()).toBe(true)
    expect(dialogEl(wrapper).open).toBe(false)
    wrapper.unmount()
  })

  it('传入提交时打开', async () => {
    const wrapper = make()
    await wrapper.setProps({ submission: submission() })

    expect(dialogEl(wrapper).open).toBe(true)
    wrapper.unmount()
  })

  it('清空提交时关闭', async () => {
    const wrapper = make({ submission: submission() })
    await wrapper.setProps({ submission: null })

    expect(dialogEl(wrapper).open).toBe(false)
    wrapper.unmount()
  })

  it('关闭事件往上抛，交给调用方清状态', async () => {
    // Esc 与 close() 都走 <dialog> 的 close 事件，父组件据此把 submission 置空
    const wrapper = make({ submission: submission() })
    dialogEl(wrapper).close()
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })

  it('点遮罩关闭', async () => {
    // <dialog> 默认不这么做，但用户预期是点了外面就该关
    const wrapper = make({ submission: submission() })
    await wrapper.find('dialog').trigger('click')

    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })

  it('点内容不关闭', async () => {
    const wrapper = make({ submission: submission() })
    await wrapper.find('.detail__title').trigger('click')

    expect(wrapper.emitted('close')).toBeUndefined()
    wrapper.unmount()
  })

  it('关闭按钮往上抛', async () => {
    const wrapper = make({ submission: submission() })
    const close = wrapper.findAll('button').find((b) => b.text() === '关闭')!
    await close.trigger('click')

    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })
})

describe('内容', () => {
  it('给出标识、提交者、分类、状态与时间', () => {
    const wrapper = make({ submission: submission() })
    const text = wrapper.text()

    expect(text).toContain('#42')
    expect(text).toContain('a:browser-x')
    expect(text).toContain('signup')
    expect(text).toContain('待处理')
    expect(text).toContain('匿名')
    wrapper.unmount()
  })

  it('完整 JSON 始终给出', () => {
    const wrapper = make({ submission: submission() })
    const json = wrapper.find('.detail__json').text()

    expect(json).toContain('"name": "张三"')
    expect(json).toContain('"grade": "2"')
    wrapper.unmount()
  })

  it('有 $display 时多一行导读，JSON 依然完整', async () => {
    const wrapper = make({
      submission: submission({
        payload: { $display: '张三 · 2 年级', name: '张三', secret: 'x' },
      }),
    })

    expect(wrapper.find('.detail__display').text()).toBe('张三 · 2 年级')
    // 导读不该把原始数据挡在后面
    expect(wrapper.find('.detail__json').text()).toContain('secret')
    wrapper.unmount()
  })

  it('没有 $display 时不显示导读那一行', () => {
    const wrapper = make({ submission: submission() })
    expect(wrapper.find('.detail__display').exists()).toBe(false)
    wrapper.unmount()
  })

  it('附件列出可下载的链接与大小', () => {
    const wrapper = make({
      submission: submission({
        files: [
          {
            id: 7,
            original_name: '作品说明.txt',
            size_bytes: 2048,
            mime: 'text/plain',
            sha256: 'abc',
            created_at: '2026-10-01T12:00:00Z',
          },
        ],
      }),
    })

    const link = wrapper.find('.detail__file a')
    expect(link.text()).toBe('作品说明.txt')
    expect(link.attributes('href')).toContain('/submissions/42/files/7')
    expect(wrapper.text()).toContain('2.0 KB')
    wrapper.unmount()
  })

  it('没有附件时明说，而不是留空', () => {
    const wrapper = make({ submission: submission() })
    expect(wrapper.find('.detail__none').text()).toBe('没有附件')
    wrapper.unmount()
  })

  it('未知状态码也照实显示', () => {
    const wrapper = make({ submission: submission({ status: 99 }) })
    expect(wrapper.text()).toContain('99')
    wrapper.unmount()
  })
})
