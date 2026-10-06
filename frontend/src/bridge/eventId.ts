/**
 * 活动标识的归一化。
 *
 * ## 为什么前端也要做一次
 *
 * 后端当然会归一化 —— 接口是对外的，用 curl 也该能写大写。但**宿主存储的命名空间
 * 不经过后端**：`cea.storage:{活动标识}:{片段}` 是这里自己拼出来的 localStorage 键。
 * 于是同一个活动经由 `/Autumn2026` 与 `/autumn2026` 进入时，会落到**两个不同的
 * 命名空间**，表现是"草稿凭空消失"—— 而它的起因（地址栏里一个字母的大小写）看起来
 * 与草稿毫无关系。
 *
 * ## 规则必须与后端逐字一致
 *
 * 后端是 `app/core/text.py` 的 `normalize_event_id`，即 `strip().lower()`。这里刻意
 * 只做同样两件事，不做 `casefold`（JS 没有对应物）、更不做 Unicode 折叠：规则简单到
 * 可以在两种语言里各写一次而不漂移，是这个设计成立的前提。规则一旦变复杂，就必须
 * 收敛成单一实现。
 *
 * 两端一致性由**内容相同的样本表**钉住：见 `eventId.spec.ts` 与
 * `backend/tests/test_text.py`，两处的样本表必须逐字相同。
 */
export function normalizeEventId(value: string | null | undefined): string {
  return (value ?? '').trim().toLowerCase()
}
