/**
 * 活动内容入口页的存在性探测。
 *
 * 抽成独立模块而不是放在 `.vue` 里：`<script setup>` 不允许具名导出，而且这类
 * 判定逻辑本来就不该依赖组件环境才能测试。
 */

/**
 * 入口页是否存在。
 *
 * 用 HEAD 而不是 GET —— 判断存在性不需要下载整个入口页。
 *
 * **网络异常时返回 true**（即"当作存在"）：断网与"没投放内容"是两个完全不同的问题，
 * 把后者说成前者会把排查引向错误的方向。真正的加载失败会在 iframe 里体现出来。
 */
export async function contentEntryExists(url: string): Promise<boolean> {
  try {
    const response = await fetch(url, { method: 'HEAD' })
    return response.ok
  } catch {
    return true
  }
}
