/**
 * 调试台要加载的来源的校验与归一化。
 *
 * 抽成独立模块而不是放在 `.vue` 里：`<script setup>` 不允许具名导出，而且这类
 * 判定逻辑本来就不该依赖组件环境才能测试（与 `event/contentProbe.ts` 同一个理由）。
 *
 * ## 为什么必须限定站内
 *
 * 调试台会把初始化消息交给被加载的页面，其中包括**身份描述符**（显示名、角色、
 * 用户标识），并把自己所在的活动变成该页面的操作代理。
 *
 * 身份描述符是刻意设计成零凭证的 —— 这正是它可以被交出去的原因，所以泄露的严重性
 * 有限。但它仍然是个人信息，而"操作代理"是实打实的能力：那个页面可以借宿主的会话
 * 向当前活动提交内容。
 *
 * 缺了这一步，调试台就是一个开放代理。因此它只接受站内路径，且只接受落在活动内容
 * 前缀下的路径 —— 放开到"任意站内路径"只会让它变成"用宿主身份打开站内任意页"的入口。
 */

/** 允许作为调试来源的前缀。两者都是活动内容：一个是草稿，一个是已投放的。 */
export const ALLOWED_SOURCE_PREFIXES = ['/draft/', '/content/'] as const

/**
 * 校验并归一化一个来源值。
 *
 * @param raw 查询参数里的原始值（可能是任意东西，来自 URL）
 * @param origin 当前站点的来源，用于判定是否站内
 * @returns 归一化后的站内路径；不合规时返回 `null`
 *
 * 用 `URL` 解析而不是字符串前缀判断：`//evil.com` 会被浏览器当作**协议相对地址**，
 * 光看"以 / 开头"会把它放过去；`http://evil.com`、`javascript:` 同理。解析之后比较
 * `origin` 一次性覆盖这些写法。
 */
export function resolveDebugSource(raw: unknown, origin: string): string | null {
  if (typeof raw !== 'string') return null

  const value = raw.trim()
  if (value === '') return null

  let url: URL
  try {
    url = new URL(value, origin)
  } catch {
    return null
  }

  if (url.origin !== origin) return null

  const allowed = ALLOWED_SOURCE_PREFIXES.some((prefix) =>
    url.pathname.startsWith(prefix),
  )
  if (!allowed) return null

  // 丢掉 hash（活动页内部的锚点与加载来源无关），保留查询串 ——
  // `/content/...?v=N` 里的版本参数是有意义的，它决定缓存行为。
  return `${url.pathname}${url.search}`
}

/** 给界面用的一句话说明，讲清被拒的原因。 */
export function describeSourceRejection(raw: unknown): string {
  if (typeof raw !== 'string' || raw.trim() === '') {
    return '没有给 src 参数。用 ?src=/draft/<名字>/index.html 指定要调试的页面。'
  }
  return (
    `不接受这个来源：${String(raw)}。` +
    `只接受站内路径，且必须落在 ${ALLOWED_SOURCE_PREFIXES.join(' 或 ')} 之下 —— ` +
    '调试台会把身份描述符与操作代理交给被加载的页面，因此不能指向站外。'
  )
}
