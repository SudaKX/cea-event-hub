/**
 * 提交领域取值与展示辅助。
 *
 * 状态码是对外契约（导出与筛选按它走），所以这里逐个钉住数值；`$display` 的取值
 * 规则则决定管理端那一格显示什么，边角（空串、非字符串、缺字段）都要有着落。
 */
import { describe, expect, it } from 'vitest'

import {
  DISPLAY_KEY,
  SUBMISSION_STATUS,
  SUBMISSION_STATUS_LABELS,
  SUBMISSION_STATUS_OPTIONS,
  parseStatusFilter,
  payloadDetail,
  payloadSummary,
  statusLabel,
  statusTone,
} from './submission'

describe('状态码', () => {
  it('码值就是约定的 1 / 0 / 2', () => {
    // 改这些数字等于改对外契约，下游导出与筛选都会跟着错
    expect(SUBMISSION_STATUS.RECEIVED).toBe(1)
    expect(SUBMISSION_STATUS.IGNORED).toBe(0)
    expect(SUBMISSION_STATUS.ACCEPTED).toBe(2)
  })

  it('只有三档，"审核中"已移除', () => {
    expect(Object.keys(SUBMISSION_STATUS)).toHaveLength(3)
    expect(Object.keys(SUBMISSION_STATUS)).not.toContain('REVIEWING')
  })

  it('每一档都有标签与配色后缀', () => {
    for (const code of Object.values(SUBMISSION_STATUS)) {
      expect(SUBMISSION_STATUS_LABELS[code]).toBeTruthy()
      expect(statusTone(code)).toBeTruthy()
    }
  })

  it('未知码值原样暴露，而不是变成空白', () => {
    // 看不见的异常比看得见的异常危险
    expect(statusLabel(99)).toContain('99')
    expect(statusTone(99)).toBe('unknown')
  })
})

describe('筛选选项', () => {
  it('第一项是"全部"，值为空串', () => {
    expect(SUBMISSION_STATUS_OPTIONS[0]).toEqual({ value: '', label: '全部' })
  })

  it('三档状态各一项，值是可解析的码值', () => {
    const codes = SUBMISSION_STATUS_OPTIONS.slice(1).map((option) =>
      parseStatusFilter(option.value),
    )
    expect(codes).toEqual([0, 1, 2])
  })
})

describe('筛选值解析', () => {
  it('空串表示不过滤', () => {
    expect(parseStatusFilter('')).toBeUndefined()
  })

  it('"0" 解析成 0，而不是被当成"空"', () => {
    // 用 `value || undefined` 这类写法会把 0 悄悄丢掉，"不采用"就永远筛不出来
    expect(parseStatusFilter('0')).toBe(0)
  })

  it('其余码值原样解析', () => {
    expect(parseStatusFilter('1')).toBe(1)
    expect(parseStatusFilter('2')).toBe(2)
  })

  it('非数字与未知码值都不发出去', () => {
    // Number('abc') 是 NaN，发出去后端只会回一个看不懂的校验错误
    expect(parseStatusFilter('abc')).toBeUndefined()
    expect(parseStatusFilter('99')).toBeUndefined()
    expect(parseStatusFilter('-1')).toBeUndefined()
  })
})

describe('$display 摘要', () => {
  it('没有该字段时退回 JSON', () => {
    expect(payloadSummary({ name: '张三' })).toBe('{"name":"张三"}')
  })

  it('有非空字符串时直接用它', () => {
    const payload = { [DISPLAY_KEY]: '张三 · 2 年级', name: '张三', grade: '2' }
    expect(payloadSummary(payload)).toBe('张三 · 2 年级')
  })

  it('空串与纯空白视为没给', () => {
    // 活动页给个空串多半是拼错了，此时显示空白比显示 JSON 更没用
    expect(payloadSummary({ [DISPLAY_KEY]: '' })).toBe('{"$display":""}')
    expect(payloadSummary({ [DISPLAY_KEY]: '   ' })).toBe('{"$display":"   "}')
  })

  it('非字符串视为没给', () => {
    // 对象或数组塞进去只会得到一段更难读的 JSON，不如老实显示原始数据
    expect(payloadSummary({ [DISPLAY_KEY]: { a: 1 } })).toContain('$display')
    expect(payloadSummary({ [DISPLAY_KEY]: 42 })).toContain('$display')
  })

  it('字段名是 $display', () => {
    expect(DISPLAY_KEY).toBe('$display')
  })
})

describe('$display 展开内容', () => {
  it('没有该字段时就是格式化 JSON', () => {
    const detail = payloadDetail({ a: 1 })
    expect(detail).toContain('"a": 1')
  })

  it('有该字段时摘要在前，完整 JSON 仍在后面', () => {
    // $display 是摘要，不该把原始数据挡在后面 —— 展开的目的常常正是看摘要没覆盖到的字段
    const detail = payloadDetail({ [DISPLAY_KEY]: '摘要', secret_field: 'x' })
    expect(detail.indexOf('摘要')).toBeLessThan(detail.indexOf('原始数据'))
    expect(detail).toContain('secret_field')
    expect(detail).toContain('"x"')
  })

  it('始终是格式化过的 JSON，不是一行挤在一起', () => {
    expect(payloadDetail({ a: 1, b: 2 })).toContain('\n')
  })
})
