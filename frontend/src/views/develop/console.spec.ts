/**
 * 动作清单的推导与日志摘要（add-develop-harness 任务 4.3）。
 *
 * 这一组钉住的核心是**清单来自协议常量，而不是手写一份**。手写清单只能断言
 * "我列的这些都在"，而真正要防的是"协议里加了新东西却忘了登记" —— 那种情况下
 * 手写清单的测试照样全绿。
 *
 * 这与路由测试里那条"清单从路由表推导，而不是手写一份"是同一个理由。
 */
import { describe, expect, it } from 'vitest'

import { BRIDGE_ERROR, BRIDGE_OP, HOST_MESSAGE } from '@/bridge/protocol'
import {
  DEFAULT_DEV_EVENT_ID,
  FORGEABLE_CODES,
  decideInterception,
  emptyInterceptState,
  filterableOps,
  filteredOps,
  forgedError,
  forgedMessage,
  formatArgs,
  formatLogTime,
  hostActions,
  summarize,
} from './console'

describe('宿主动作清单', () => {
  it('默认覆盖协议里的每一个宿主消息类型', () => {
    const types = hostActions().map((action) => action.type)
    for (const value of Object.values(HOST_MESSAGE)) {
      expect(types, `${value} 没有出现在面板上`).toContain(value)
    }
  })

  it('协议新增一个类型时，它自动出现（这就是"从真源推导"要保证的性质）', () => {
    // 模拟"有人往 HOST_MESSAGE 里加了一条，但没来改面板"
    const extended = [...Object.values(HOST_MESSAGE), 'hub:brand-new']

    const types = hostActions(extended).map((action) => action.type)

    expect(types).toContain('hub:brand-new')
  })

  it('认不出来的类型按直接发送处理，而不是被丢掉', () => {
    const [action] = hostActions(['hub:brand-new'])
    expect(action?.kind).toBe('raw')
    expect(action?.hint).toContain('新类型')
  })

  it('重握手与会话推送走宿主真实方法，其余直接发送', () => {
    const byType = new Map(hostActions().map((a) => [a.type, a.kind]))
    // 这两条必须走真实路径：重握手要连带重置宿主的内部状态
    expect(byType.get(HOST_MESSAGE.INIT)).toBe('handshake')
    expect(byType.get(HOST_MESSAGE.SESSION)).toBe('session')
    // 主题宿主从不主动发；结果与上传进度需要面板补上下文
    expect(byType.get(HOST_MESSAGE.THEME)).toBe('raw')
    expect(byType.get(HOST_MESSAGE.RESULT)).toBe('raw')
    expect(byType.get(HOST_MESSAGE.UPLOAD_PROGRESS)).toBe('raw')
  })

  it('每个动作都带一句说明（面板上不能有光秃秃的按钮）', () => {
    for (const action of hostActions()) {
      expect(action.hint.length, `${action.type} 没有说明`).toBeGreaterThan(0)
    }
  })
})

describe('拦截判据', () => {
  /*
    这是整套拦截逻辑的唯一判据，抽成纯函数就是为了能在这里把它钉死。
    三种结果的区分是刻意的：`null` = 放行（面板什么都不做）、`ask` = 挂起等人选、
    `fail` / `forward` = 按默认应答直接结算。
  */
  const base = emptyInterceptState()

  it('没勾中的操作一律放行', () => {
    expect(decideInterception(base, 'form.submit')).toBeNull()
    expect(
      decideInterception({ ...base, filters: { 'storage.save': true } }, 'form.submit'),
    ).toBeNull()
  })

  it('勾中但没开默认应答时挂起（这一档才能看清内容再决定）', () => {
    const state = { ...base, filters: { 'form.submit': true } }
    expect(decideInterception(state, 'form.submit')).toBe('ask')
  })

  it('勾中且开了默认应答时按默认动作结算', () => {
    const fail = {
      ...base,
      filters: { 'form.submit': true },
      autoRespond: true,
      defaultAction: 'fail' as const,
    }
    expect(decideInterception(fail, 'form.submit')).toBe('fail')

    const forward = { ...fail, defaultAction: 'forward' as const }
    expect(decideInterception(forward, 'form.submit')).toBe('forward')
  })

  it('默认应答**默认是关的**', () => {
    // 开着会让"拦截"从一个观察动作变成静默改写结果的动作
    expect(emptyInterceptState().autoRespond).toBe(false)
  })

  it('过滤为空时不拦任何东西', () => {
    for (const op of Object.values(BRIDGE_OP)) {
      expect(decideInterception(base, op), `${op} 不该被拦`).toBeNull()
    }
  })

  it('非字符串的操作名不拦（不能因为一条怪消息就挂起）', () => {
    expect(decideInterception({ ...base, filters: { x: true } }, undefined)).toBeNull()
    expect(decideInterception({ ...base, filters: { x: true } }, 42)).toBeNull()
  })
})

describe('可过滤的操作清单', () => {
  it('覆盖协议里的每一个操作', () => {
    const ops = filterableOps()
    for (const value of Object.values(BRIDGE_OP)) {
      expect(ops, `${value} 不在可过滤清单里`).toContain(value)
    }
  })

  it('协议新增操作时自动出现', () => {
    expect(filterableOps([...Object.values(BRIDGE_OP), 'form.newOp'])).toContain('form.newOp')
  })
})

describe('勾选摘要与请求内容', () => {
  it('只列出真正勾中的操作', () => {
    expect(filteredOps({ a: true, b: false, c: true })).toEqual(['a', 'c'])
    expect(filteredOps({})).toEqual([])
  })

  it('请求内容按 JSON 展开，便于看清发的是什么', () => {
    const text = formatArgs({ payload: { name: '张三' }, kind: 'apply' })
    expect(text).toContain('"name": "张三"')
    expect(text).toContain('"kind": "apply"')
  })

  it('超长内容被截断并标明（不能让一条请求把面板撑爆）', () => {
    const text = formatArgs({ blob: 'x'.repeat(2000) }, 100)
    expect(text.length).toBeLessThan(200)
    expect(text).toContain('已截断')
  })

  it('序列化不了的内容也不抛异常', () => {
    const cyclic: Record<string, unknown> = {}
    cyclic.self = cyclic
    expect(() => formatArgs(cyclic)).not.toThrow()
  })
})

describe('可伪造的错误码', () => {
  it('挑的是真后端造不出来或很难造的那几个', () => {
    // quota_exhausted / event_closed 这类能真造，不该出现在这里 ——
    // 让后端真的拒绝你，验到的才是真东西
    expect(FORGEABLE_CODES).toContain(BRIDGE_ERROR.TIMEOUT)
    expect(FORGEABLE_CODES).toContain(BRIDGE_ERROR.UNSUPPORTED)
    expect(FORGEABLE_CODES).not.toContain(BRIDGE_ERROR.QUOTA_EXHAUSTED)
  })

  it('造出来的错误形状与宿主真实回的那条一致', () => {
    const error = forgedError(BRIDGE_ERROR.TIMEOUT)
    expect(error.code).toBe(BRIDGE_ERROR.TIMEOUT)
    expect(error.message).toBe(forgedMessage(BRIDGE_ERROR.TIMEOUT))
  })

  it('每个可伪造的码都有一句像样的默认文案', () => {
    for (const code of FORGEABLE_CODES) {
      const message = forgedMessage(code)
      expect(message.length, `${code} 没有文案`).toBeGreaterThan(0)
      expect(message).not.toContain('操作失败，请稍后重试') // 那是兜底，不是专属文案
    }
  })
})

describe('日志摘要', () => {
  it('把请求压成 op 与参数名', () => {
    const text = summarize({
      v: 1,
      type: 'event:rpc',
      id: 'r3',
      payload: { op: 'form.submit', args: { payload: {}, kind: 'apply' } },
    })
    expect(text).toContain('id=r3')
    expect(text).toContain('op=form.submit')
    expect(text).toContain('payload')
    expect(text).toContain('kind')
  })

  it('失败的应答显示错误码', () => {
    const text = summarize({
      v: 1,
      type: 'hub:result',
      id: 'r3',
      payload: { ok: false, error: { code: 'quota_exhausted', message: '名额已满' } },
    })
    expect(text).toContain('error=quota_exhausted')
    expect(text).toContain('名额已满')
  })

  it('上传进度在 total 缺失时只显示已传字节', () => {
    const withTotal = summarize({
      type: 'hub:upload-progress',
      payload: { requestId: 'r1', loaded: 50, total: 100 },
    })
    expect(withTotal).toContain('loaded=50/100')

    const withoutTotal = summarize({
      type: 'hub:upload-progress',
      payload: { requestId: 'r1', loaded: 50, total: null },
    })
    expect(withoutTotal).toContain('loaded=50')
    expect(withoutTotal).not.toContain('/null')
  })

  it('认不出来的形状也不抛异常（日志不能因为一条怪消息就断掉）', () => {
    expect(() => summarize(null)).not.toThrow()
    expect(() => summarize('就是一段字符串')).not.toThrow()
    expect(() => summarize({ type: 'hub:init' })).not.toThrow()
    expect(summarize(undefined)).toBe('undefined')
  })
})

describe('默认活动标识', () => {
  it('与后端 Settings.DEV_EVENT_ID 的默认值一致', () => {
    // 两处各写一份是刻意的：一个在前端常量里、一个在后端配置里。
    // 这条断言的作用是让它成为一个**被声明的**约定，而不是一个巧合。
    expect(DEFAULT_DEV_EVENT_ID).toBe('dev')
  })
})

describe('日志时刻', () => {
  it('精确到秒，且补零', () => {
    // 用本地时间构造，避免断言依赖运行时区
    const at = new Date(2026, 9, 6, 9, 5, 3).getTime()
    expect(formatLogTime(at)).toBe('09:05:03')
  })

  it('毫秒被丢掉（它对读日志没有帮助，只会占宽度）', () => {
    const at = new Date(2026, 9, 6, 13, 4, 7, 987).getTime()
    expect(formatLogTime(at)).toBe('13:04:07')
  })
})
