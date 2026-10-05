/**
 * 登出：请求服务端结束会话，然后把用户送回登录页。
 *
 * **抽出来是因为它需要存在于不止一处。** 原先只有管理台外壳里有登出，于是非管理员
 * 登录之后没有出口 —— 只能去清浏览器数据。个人中心补上了入口，而两处各写一遍
 * "登出之后去哪"迟早会给出不同答案（上一个变更刚因为同一份渲染写两遍而漏改一处）。
 *
 * 它只共享**动作**，不共享外观：管理台那一处在侧栏底部（与"返回主页"同组），个人
 * 中心那一处是页面里的主操作之一，两者的渲染本来就不一样。
 */
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

export function useSignOut() {
  const auth = useAuthStore()
  const router = useRouter()

  /**
   * 结束当前会话并回到登录页。
   *
   * **顺序不能反。** `signOut()` 先请求服务端让会话失效并清掉本地用户，之后才导航。
   * 反过来的话，会有一瞬间"已经离开当前页面、但本地仍认为自己是登录态"，而目标页
   * 若需要登录就会把用户弹回来。
   *
   * **这里不做错误处理，因为 store 已经决定了策略**：`signOut()` 把登出当成尽力而为
   * —— 服务端可能已经删了会话，也可能网络不通，无论哪种情况本地状态都必须清掉，
   * 否则界面会停在一个已失效的身份上。它因此从不抛错，这里再包一层 try/catch 只会
   * 得到永远不会执行的代码。
   */
  async function signOut(): Promise<void> {
    await auth.signOut()
    await router.push({ name: 'login' })
  }

  return { signOut }
}
