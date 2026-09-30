/**
 * 任务 13.3（续）：内容入口页的存在性探测。
 *
 * 活动存在但内容没投放，与"内容加载失败"是两个不同的问题。把后者说成前者会把
 * 排查引向错误方向，因此网络异常时**不**报"内容缺失"。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import { contentEntryExists } from './contentProbe'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('内容入口页探测', () => {
  it('入口页存在时返回 true', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200 }))
    await expect(contentEntryExists('/content/e/index.html?v=1')).resolves.toBe(true)
  })

  it('入口页缺失时返回 false', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404 }))
    await expect(contentEntryExists('/content/e/index.html?v=1')).resolves.toBe(false)
  })

  it('用 HEAD 而不是 GET，避免白下载整个入口页', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)

    await contentEntryExists('/content/e/index.html')

    expect(fetchMock).toHaveBeenCalledWith('/content/e/index.html', { method: 'HEAD' })
  })

  it('网络异常时不误报内容缺失', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network down')))
    await expect(contentEntryExists('/content/e/index.html')).resolves.toBe(true)
  })
})
