/**
 * 任务 13.1：协议守卫与操作表。
 */
import { describe, expect, it } from 'vitest'

import {
  ALLOWED_OPS,
  BRIDGE_ERROR,
  BRIDGE_OP,
  PROTOCOL_VERSION,
  isCompatible,
  isEnvelope,
} from './protocol'

describe('信封守卫', () => {
  it('接受形状合法的信封', () => {
    expect(isEnvelope({ v: 1, type: 'hub:init' })).toBe(true)
    expect(isEnvelope({ v: 1, type: 'event:rpc', id: 'r1', payload: {} })).toBe(true)
  })

  it('拒绝缺少版本或类型的消息', () => {
    expect(isEnvelope({ type: 'hub:init' })).toBe(false)
    expect(isEnvelope({ v: 1 })).toBe(false)
    expect(isEnvelope({ v: '1', type: 'hub:init' })).toBe(false)
  })

  it('拒绝空类型', () => {
    expect(isEnvelope({ v: 1, type: '' })).toBe(false)
  })

  it('拒绝 id 类型不对的消息', () => {
    expect(isEnvelope({ v: 1, type: 'x', id: 42 })).toBe(false)
  })

  it('拒绝非对象', () => {
    for (const value of [null, undefined, 'text', 42, []]) {
      expect(isEnvelope(value)).toBe(false)
    }
  })
})

describe('版本兼容', () => {
  it('主版本一致即兼容', () => {
    expect(isCompatible(PROTOCOL_VERSION)).toBe(true)
    expect(isCompatible(PROTOCOL_VERSION + 0.5)).toBe(true)
  })

  it('主版本不同即不兼容', () => {
    expect(isCompatible(PROTOCOL_VERSION + 1)).toBe(false)
    expect(isCompatible(0)).toBe(false)
  })

  it('非数字一律不兼容', () => {
    expect(isCompatible('1')).toBe(false)
    expect(isCompatible(undefined)).toBe(false)
    expect(isCompatible(null)).toBe(false)
  })
})

describe('操作白名单', () => {
  it('是一张固定表，不是任意字符串', () => {
    expect(ALLOWED_OPS.length).toBeGreaterThan(0)
    for (const op of ALLOWED_OPS) {
      expect(typeof op).toBe('string')
      expect(op).toMatch(/^[a-z]+\.[a-zA-Z]+$/)
    }
  })

  it('包含提交、读取与草稿操作', () => {
    expect(ALLOWED_OPS).toContain(BRIDGE_OP.FORM_SUBMIT)
    expect(ALLOWED_OPS).toContain(BRIDGE_OP.FORM_SUBMIT_FILES)
    expect(ALLOWED_OPS).toContain(BRIDGE_OP.EVENT_INFO)
    expect(ALLOWED_OPS).toContain(BRIDGE_OP.DRAFT_SAVE)
  })

  it('不包含任何管理类操作', () => {
    // 桥接层绝不能触达管理接口 —— 那正是"零凭证代理"要防的事
    for (const op of ALLOWED_OPS) {
      expect(op).not.toMatch(/admin|user\.(create|delete|update)|event\.(create|delete)/)
    }
  })
})

describe('错误码表', () => {
  it('四种"不能提交"的原因各不相同', () => {
    const codes = [
      BRIDGE_ERROR.LOGIN_REQUIRED,
      BRIDGE_ERROR.EVENT_CLOSED,
      BRIDGE_ERROR.QUOTA_EXHAUSTED,
      BRIDGE_ERROR.RATE_LIMITED,
    ]
    expect(new Set(codes).size).toBe(4)
  })

  it('包含宿主自身的失败原因', () => {
    expect(BRIDGE_ERROR.UNSUPPORTED).toBeDefined()
    expect(BRIDGE_ERROR.TIMEOUT).toBeDefined()
    expect(BRIDGE_ERROR.CANCELLED).toBeDefined()
    expect(BRIDGE_ERROR.VERSION_MISMATCH).toBeDefined()
  })
})
