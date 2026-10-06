/**
 * 活动标识归一化的样本表 —— **与后端共用同一份内容**。
 *
 * 对照物是 `backend/tests/test_text.py` 里的 `NORMALIZATION_SAMPLES`。两处的表必须
 * 逐字相同：改一处就要改另一处，否则会出现"后端认得出、前端认不出"的间歇性故障
 * （草稿命名空间分叉、或内容地址拼错）。
 *
 * 表里刻意放了几个刁钻样本（全角字母、`İ`、`ẞ`、希腊语词尾 sigma、标题格合字），
 * 它们正是 `String.prototype.toLowerCase()` 与 Python `str.lower()` 最可能分叉的地方。
 * 期望值是**实测**出来的：两端在这些样本上完全一致（含希腊语词尾 sigma 的上下文相关
 * 小写化）。
 */
import { describe, expect, it } from 'vitest'

import { normalizeEventId } from './eventId'

/** 与 `backend/tests/test_text.py` 的 NORMALIZATION_SAMPLES 逐字相同。 */
const NORMALIZATION_SAMPLES: Array<[string, string]> = [
  ['autumn2026', 'autumn2026'],
  ['Autumn2026', 'autumn2026'],
  ['AUTUMN2026', 'autumn2026'],
  ['  Autumn-2026  ', 'autumn-2026'],
  ['Autumn_2026', 'autumn_2026'],
  // 全角：两端都得到全角小写（FF41 FF42 FF43），随后会被后端形态校验拒掉 —— 不是 ASCII
  ['ＡＢＣ', 'ａｂｃ'],
  // i + 组合上点（0069 0307 …）：长度变了，所以后端必须"先归一化再校验"
  ['İstanbul', 'i\u0307stanbul'],
  ['ẞ', 'ß'],
  ['Straße', 'straße'],
  // 希腊语词尾 sigma 是**上下文相关**的（… 03C6 03BF 03C2）：两端都必须给出 ς
  ['ΣΟΦΟΣ', '\u03c3\u03bf\u03c6\u03bf\u03c2'],
  ['ǅungla', '\u01c6ungla'],
  ['', ''],
  ['  ', ''],
]

describe('normalizeEventId', () => {
  it.each(NORMALIZATION_SAMPLES)('%j -> %j', (raw, expected) => {
    expect(normalizeEventId(raw)).toBe(expected)
  })

  it('归一化是幂等的', () => {
    for (const [, expected] of NORMALIZATION_SAMPLES) {
      expect(normalizeEventId(expected)).toBe(expected)
    }
  })

  it('null 与 undefined 归一化为空串而不是抛错', () => {
    // 路由参数在极端情况下可能是 undefined；归一化不该成为新的崩溃点
    expect(normalizeEventId(null)).toBe('')
    expect(normalizeEventId(undefined)).toBe('')
  })
})
