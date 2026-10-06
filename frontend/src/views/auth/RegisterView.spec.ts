/**
 * 注册页与核销页（任务 16.6）。
 *
 * 两阶段注册的用户可见部分是三个状态：填表 → 查收邮件 → 点链接后建号。这里盯的是
 * 三者之间的**措辞与路由**对着没有 —— 尤其"重入"那一种，它其实没有新邮件发出，
 * 说成"已发送"会让用户去邮箱里找一封不存在的信。
 */
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

const { register, verifyRegistration } = vi.hoisted(() => ({
  register: vi.fn(),
  verifyRegistration: vi.fn(),
}))

vi.mock('@/api/auth', async () => {
  const actual = await vi.importActual<typeof import('@/api/auth')>('@/api/auth')
  return {
    ...actual,
    register: (...args: unknown[]) => register(...args),
    verifyRegistration: (...args: unknown[]) => verifyRegistration(...args),
  }
})

import { ApiError } from '@/api/client'
import RegisterView from './RegisterView.vue'
import VerifyRegistrationView from './VerifyRegistrationView.vue'

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/register', name: 'register', component: RegisterView },
      { path: '/login', name: 'login', component: { template: '<div />' } },
      {
        path: '/verify-registration',
        name: 'verify-registration',
        component: VerifyRegistrationView,
      },
    ],
  })
}

async function mountRegister() {
  const router = makeRouter()
  await router.push('/register')
  await router.isReady()

  const wrapper = mount(RegisterView, {
    global: { plugins: [router, createPinia()] },
    attachTo: document.body,
  })
  return { wrapper, router }
}

async function mountVerify(query: string) {
  const router = makeRouter()
  await router.push(`/verify-registration${query}`)
  await router.isReady()

  const wrapper = mount(VerifyRegistrationView, {
    global: { plugins: [router, createPinia()] },
    attachTo: document.body,
  })
  await wrapper.vm.$nextTick()
  return { wrapper, router }
}

/** 按标签找到输入框 */
function inputFor(wrapper: ReturnType<typeof mount>, label: string) {
  const field = wrapper.findAll('.field').find((f) => f.find('.field__label').text().startsWith(label))
  if (!field) throw new Error(`没有找到「${label}」这个字段`)
  return field.find('input')
}

beforeEach(() => {
  vi.clearAllMocks()
  setActivePinia(createPinia())
})

describe('注册表单', () => {
  it('邮箱是必填项，没填就不能提交', async () => {
    const { wrapper } = await mountRegister()

    await inputFor(wrapper, '用户名').setValue('alice')
    await inputFor(wrapper, '密码').setValue('correct-horse')
    // 邀请码现在是必填的，一并填上 —— 否则这条用例会因为"另一个字段没填"而失败，
    // 而它要验的是邮箱
    await inputFor(wrapper, '邀请码').setValue('ABCDEFGHJK')
    expect(wrapper.find('button[type="submit"]').attributes('disabled')).toBeDefined()

    await inputFor(wrapper, '邮箱').setValue('alice@example.com')
    expect(wrapper.find('button[type="submit"]').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('有邀请码字段，且它是必填的', async () => {
    /*
      **这条断言的方向被本变更反转过。** 它原先写的是"不再有邀请码字段"，依据是
      任务 4.8 移除了那个共享口令开关。现在准入换成了一套真正的邀请码机制，字段
      回来了 —— 留着旧断言会让"注册页有邀请码字段"这件事永远测不过。
    */
    const { wrapper } = await mountRegister()

    expect(wrapper.text()).toContain('邀请码')
    const field = inputFor(wrapper, '邀请码')
    expect(field.exists()).toBe(true)

    // 其余三项都填了、只有邀请码空着时，仍然不能提交
    await inputFor(wrapper, '用户名').setValue('alice')
    await inputFor(wrapper, '密码').setValue('correct-horse')
    await inputFor(wrapper, '邮箱').setValue('alice@example.com')
    expect(wrapper.find('button[type="submit"]').attributes('disabled')).toBeDefined()

    await field.setValue('ABCDEFGHJK')
    expect(wrapper.find('button[type="submit"]').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('提交时把邀请码带上', async () => {
    register.mockResolvedValue({ ongoing: false })
    const { wrapper } = await mountRegister()

    await inputFor(wrapper, '用户名').setValue('alice')
    await inputFor(wrapper, '密码').setValue('correct-horse')
    await inputFor(wrapper, '邮箱').setValue('alice@example.com')
    await inputFor(wrapper, '邀请码').setValue('ABCDEFGHJK')
    await wrapper.find('form').trigger('submit')

    await vi.waitFor(() =>
      expect(register).toHaveBeenCalledWith(
        expect.objectContaining({ invitation_code: 'ABCDEFGHJK' }),
      ),
    )
    wrapper.unmount()
  })

  it('邀请码不可用时把提示落在该字段上', async () => {
    /*
      服务端只回一句"邀请码不可用"，不区分原因 —— 注册接口匿名可达，区分原因等于
      把它变成邀请码枚举器。界面因此只能照原样显示。
    */
    register.mockRejectedValue(
      new ApiError('validation_failed', '提交内容有误', 422, {
        invitation_code: '邀请码不可用',
      }),
    )
    const { wrapper } = await mountRegister()

    await inputFor(wrapper, '用户名').setValue('alice')
    await inputFor(wrapper, '密码').setValue('correct-horse')
    await inputFor(wrapper, '邮箱').setValue('alice@example.com')
    await inputFor(wrapper, '邀请码').setValue('WRONGCODE1')
    await wrapper.find('form').trigger('submit')

    await vi.waitFor(() => expect(wrapper.text()).toContain('邀请码不可用'))
    wrapper.unmount()
  })

  it('提交后进入"查收邮件"，而不是"注册成功"', async () => {
    register.mockResolvedValue({ ongoing: false })
    const { wrapper, router } = await mountRegister()

    await inputFor(wrapper, '用户名').setValue('alice')
    await inputFor(wrapper, '密码').setValue('correct-horse')
    await inputFor(wrapper, '邮箱').setValue('alice@example.com')
    await wrapper.find('form').trigger('submit')

    // 等**导航**而不是等接口：接口调用之后还有一次 replace
    await vi.waitFor(() => expect(router.currentRoute.value.query.sent).toBe('1'))
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('查收邮件')
    // 账号此时**还不存在**，所以不能说"注册成功"
    expect(wrapper.text()).not.toContain('注册成功')
    wrapper.unmount()
  })

  it('重入时不声称发了新邮件', async () => {
    /*
      重入意味着同一对用户名与邮箱已有一条待验证的占位，本次**没有新邮件**。
      说成"已发送"会让用户去邮箱里找一封不存在的信。
    */
    register.mockResolvedValue({ ongoing: true })
    const { wrapper, router } = await mountRegister()

    await inputFor(wrapper, '用户名').setValue('alice')
    await inputFor(wrapper, '密码').setValue('correct-horse')
    await inputFor(wrapper, '邮箱').setValue('alice@example.com')
    await wrapper.find('form').trigger('submit')
    await vi.waitFor(() => expect(router.currentRoute.value.query.ongoing).toBe('1'))
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('没有重复发送')
    expect(wrapper.text()).not.toContain('验证邮件已发送')
    wrapper.unmount()
  })

  it('刷新之后仍然停在"查收邮件"', async () => {
    // 用 query 而不是组件内的 ref 就是为了这个：邮件确实发过了，不该因为刷新而
    // 退回一个空表单，让用户以为没提交上去
    const { wrapper } = await mountRegister()
    const router = wrapper.vm.$router
    await router.replace({ query: { sent: '1' } })
    await wrapper.vm.$nextTick()

    expect(wrapper.find('form').exists()).toBe(false)
    expect(wrapper.text()).toContain('查收邮件')
    wrapper.unmount()
  })

  it('冲突提示显示在红色错误卡片里，且用更具体的那一句', async () => {
    /*
      **这条断言的方向被改过。** 它原先查的是字段下面那行小字（`.field__error`），
      但同一件事因此被说了两遍：卡片写笼统的 `message`（"提交内容有误"），字段下面
      写具体原因。屏幕上先看到的反而是没用那句。

      现在只有一处：红色卡片，且**优先用字段里的具体原因** —— 它才能让人据以行动。
    */
    register.mockRejectedValue(
      new ApiError('registration_pending', '该用户名或邮箱有一条待验证的注册', 409, {
        username: '该用户名有一条待验证的注册',
      }),
    )
    const { wrapper, router } = await mountRegister()

    await inputFor(wrapper, '用户名').setValue('alice')
    await inputFor(wrapper, '密码').setValue('correct-horse')
    await inputFor(wrapper, '邮箱').setValue('alice@example.com')
    await inputFor(wrapper, '邀请码').setValue('ABCDEFGHJK')
    await wrapper.find('form').trigger('submit')

    await vi.waitFor(() => expect(wrapper.find('[role="alert"]').exists()).toBe(true))
    expect(wrapper.find('[role="alert"]').text()).toContain('待验证')
    // 字段下面不再重复一遍
    expect(wrapper.find('.field__error').exists()).toBe(false)

    // 没跳到"已发送"，因为这次并没有建立占位
    expect(router.currentRoute.value.query.sent).toBeUndefined()
    wrapper.unmount()
  })

  it('只显示一条错误，且不是那句笼统的', async () => {
    /* 邀请码不通过时的原始症状：卡片一句 + 字段一句，共两处。 */
    register.mockRejectedValue(
      new ApiError('validation_failed', '提交内容有误', 422, {
        invitation_code: '邀请码不可用',
      }),
    )
    const { wrapper } = await mountRegister()

    await inputFor(wrapper, '用户名').setValue('alice')
    await inputFor(wrapper, '密码').setValue('correct-horse')
    await inputFor(wrapper, '邮箱').setValue('alice@example.com')
    await inputFor(wrapper, '邀请码').setValue('WRONGCODE1')
    await wrapper.find('form').trigger('submit')

    await vi.waitFor(() => expect(wrapper.find('[role="alert"]').exists()).toBe(true))
    expect(wrapper.findAll('[role="alert"]')).toHaveLength(1)
    expect(wrapper.find('[role="alert"]').text()).toBe('邀请码不可用')
    expect(wrapper.text()).not.toContain('提交内容有误')
    wrapper.unmount()
  })
})

describe('核销页', () => {
  it('带令牌进入即自动提交', async () => {
    verifyRegistration.mockResolvedValue(undefined)
    const { wrapper } = await mountVerify('?token=abc123')
    await vi.waitFor(() => expect(verifyRegistration).toHaveBeenCalledWith('abc123'))

    await vi.waitFor(() => expect(wrapper.text()).toContain('账号已创建'))
    wrapper.unmount()
  })

  it('成功后给出去登录的入口，而不是自动登录', async () => {
    // "注册"与"获得会话"是两件明确的事
    verifyRegistration.mockResolvedValue(undefined)
    const { wrapper } = await mountVerify('?token=abc123')
    await vi.waitFor(() => expect(wrapper.text()).toContain('账号已创建'))

    expect(wrapper.find('a[href="/login"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('缺令牌参数时直接失败，不请求接口', async () => {
    const { wrapper } = await mountVerify('')
    await vi.waitFor(() => expect(wrapper.find('[role="alert"]').exists()).toBe(true))

    expect(verifyRegistration).not.toHaveBeenCalled()
    expect(wrapper.find('[role="alert"]').text()).toContain('缺少令牌参数')
    wrapper.unmount()
  })

  it('链接过期或已用完时给出重来的出路', async () => {
    verifyRegistration.mockRejectedValue(
      new ApiError('token_invalid', '凭据无效或已过期', 400),
    )
    const { wrapper } = await mountVerify('?token=stale')
    await vi.waitFor(() => expect(wrapper.find('[role="alert"]').exists()).toBe(true))

    expect(wrapper.find('[role="alert"]').text()).toContain('凭据无效或已过期')
    // 用户能做的事只有一件：回注册页用同样的信息再提交一次
    expect(wrapper.find('a[href="/register"]').exists()).toBe(true)
    wrapper.unmount()
  })
})
