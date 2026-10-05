/**
 * 提交详情对话框的**内容**。
 *
 * 开合、遮罩、焦点陷阱这些外壳行为都在 `Modal` 里，由 `Modal.spec.ts` 覆盖；
 * 这里只管"这一条提交要显示成什么样"。
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

describe('提交者那一栏', () => {
  it('账号已删除时显示 u:{id} 加「已删除」，而不是「匿名」', () => {
    /*
      **这里漏过一次。** 列表与详情原先各写了一遍"匿名"的判断，给已删除账号补标记时
      只改了列表 —— 详情里那条提交仍然顶着「匿名」。两者因此抽成同一个组件，这条
      用例守着详情这一侧。
    */
    const wrapper = make({
      submission: submission({
        submitter: 'u:7',
        submitter_display: null,
        submitter_deleted: true,
        from_authenticated_user: true,
      }),
    })

    const cell = wrapper.find('.submitter')
    expect(cell.find('.cell').text()).toBe('u:7')
    expect(cell.find('.tag').text()).toBe('已删除')
    expect(cell.find('.tag').classes()).toContain('tag--deleted')
    expect(cell.text()).not.toContain('匿名')
    wrapper.unmount()
  })

  it('匿名访客显示「匿名」，而不是「已删除」', () => {
    const wrapper = make({ submission: submission() })

    const cell = wrapper.find('.submitter')
    expect(cell.find('.tag').text()).toBe('匿名')
    expect(cell.text()).not.toContain('已删除')
    wrapper.unmount()
  })

  it('账号仍在时显示显示名，且两个标记都不出现', () => {
    const wrapper = make({
      submission: submission({
        submitter: 'u:2',
        submitter_display: '张三',
        submitter_deleted: false,
        from_authenticated_user: true,
      }),
    })

    const cell = wrapper.find('.submitter')
    expect(cell.find('.cell').text()).toBe('张三')
    expect(cell.find('.tag').exists()).toBe(false)
    wrapper.unmount()
  })
})

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

  it('标题带上提交编号，一眼知道在看哪一条', () => {
    const wrapper = make({ submission: submission() })
    expect(wrapper.find('.modal__title').text()).toBe('提交 #42')
    wrapper.unmount()
  })
})
