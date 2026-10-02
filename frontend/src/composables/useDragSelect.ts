/**
 * 按住滑动多选。
 *
 * 列表页通用：在某一项上按下 → **按住并离开起点** → 滑过的项刷成起点那个值 →
 * 松开复位。
 *
 * 抽出来是因为这段状态机有细节（"按下"与"真的滑起来"是两回事），抄一份迟早走样。
 * 提交页与用户页共用同一份。
 *
 * 分工：调用方负责渲染复选框并把事件转过来，这里只维护"现在是不是在拖、要刷成
 * 什么值"。复选框自己并不知道兄弟，所以拖动必须由列表统筹。
 */
import { onBeforeUnmount, onMounted, ref, type Ref } from 'vue'

export function useDragSelect(selected: Ref<Set<number>>) {
  /** 指针已在某一项上按下，但还没离开它 —— 此时还只是普通的一次点击 */
  const armed = ref(false)
  /** 真的滑起来了 */
  const dragging = ref(false)
  /** 这一轮拖动要把滑过的项刷成什么值 */
  const dragValue = ref(false)

  /** 只在值真的变了才换新 Set，避免无谓的重渲染 */
  function set(id: number, on: boolean): void {
    if (selected.value.has(id) === on) return
    const next = new Set(selected.value)
    if (on) next.add(id)
    else next.delete(id)
    selected.value = next
  }

  /** 按下：记下起点与目标状态，但**先不进入拖动** */
  function onPress(value: boolean): void {
    armed.value = true
    dragging.value = false
    dragValue.value = value
  }

  /**
   * 按住状态下离开了起点 —— 这时才开始滑。
   *
   * 挂在**每一个**复选框上（`pointerleave` 不冒泡，只能各自听）。无妨：`armed`
   * 只在按下到松开之间为真，而那时第一次离开的必然是起点；之后的离开只是把已经
   * 是 true 的 `dragging` 再置一次。
   */
  function onLeave(): void {
    if (armed.value) dragging.value = true
  }

  /** 滑过某一项 */
  function onEnter(id: number): void {
    // 只在真的滑起来之后才生效。否则鼠标扫过列表就会乱改选择
    if (!dragging.value) return
    set(id, dragValue.value)
  }

  function stop(): void {
    armed.value = false
    dragging.value = false
  }

  // 指针可能在列表之外松开，所以听 window
  onMounted(() => window.addEventListener('pointerup', stop))
  onBeforeUnmount(() => window.removeEventListener('pointerup', stop))

  return { set, onPress, onLeave, onEnter }
}
