/**
 * 任务 12.3：错误解包与 401 清态。
 *
 * 逻辑抽成了纯函数，因此不必起 HTTP 服务就能验证错误形状的映射。
 */
import type { AxiosError } from 'axios'
import { describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  setUnauthorizedHandler,
  toApiError,
  triggerUnauthorized,
  type ApiErrorBody,
} from './client'

function axiosError(
  status: number | undefined,
  body?: ApiErrorBody,
  message = 'Request failed',
): AxiosError<ApiErrorBody> {
  return {
    isAxiosError: true,
    message,
    response: status === undefined ? undefined : { status, data: body },
  } as unknown as AxiosError<ApiErrorBody>
}

describe('错误解包', () => {
  it('读取后端统一信封', () => {
    const error = toApiError(
      axiosError(409, {
        error: { code: 'quota_exhausted', message: '该活动名额已满' },
      }),
    )

    expect(error).toBeInstanceOf(ApiError)
    expect(error.code).toBe('quota_exhausted')
    expect(error.message).toBe('该活动名额已满')
    expect(error.status).toBe(409)
  })

  it('保留字段级错误，供表单标注到具体输入框', () => {
    const error = toApiError(
      axiosError(422, {
        error: {
          code: 'validation_failed',
          message: '提交内容有误',
          fields: { contact: '手机号格式不正确' },
        },
      }),
    )

    expect(error.fields).toEqual({ contact: '手机号格式不正确' })
  })

  it('没有响应体时兜底为 network_error', () => {
    const error = toApiError(axiosError(undefined))
    expect(error.code).toBe('network_error')
    expect(error.status).toBe(0)
  })

  it('有状态码但没有信封时兜底为 unknown_error', () => {
    // 例如反向代理返回的 HTML 错误页
    const error = toApiError(axiosError(502, undefined, 'Bad Gateway'))
    expect(error.code).toBe('unknown_error')
    expect(error.status).toBe(502)
  })

  it('错误码与状态码可区分四种"不能提交"', () => {
    const codes = [
      toApiError(axiosError(401, { error: { code: 'login_required', message: '' } })),
      toApiError(axiosError(403, { error: { code: 'event_closed', message: '' } })),
      toApiError(axiosError(409, { error: { code: 'quota_exhausted', message: '' } })),
      toApiError(axiosError(429, { error: { code: 'rate_limited', message: '' } })),
    ].map((error) => `${error.status}:${error.code}`)

    expect(new Set(codes).size).toBe(4)
  })
})

describe('401 处理', () => {
  it('触发注册过的处理器', () => {
    const handler = vi.fn()
    setUnauthorizedHandler(handler)

    triggerUnauthorized()

    expect(handler).toHaveBeenCalledTimes(1)
    setUnauthorizedHandler(null)
  })

  it('没有处理器时不报错', () => {
    setUnauthorizedHandler(null)
    expect(() => triggerUnauthorized()).not.toThrow()
  })
})
