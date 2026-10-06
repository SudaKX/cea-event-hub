<script setup lang="ts">
/**
 * 活动页宿主。
 *
 * 把活动内容挂在**沙箱 iframe** 里，并通过 postMessage 桥接与其交互。三条不可
 * 动摇的约束：
 *
 * 1. `sandbox` **绝不含 `allow-same-origin`**。同源 iframe 一旦拿到这个令牌，
 *    沙箱等于没有：它能访问 `parent.document`，读到宿主内存里的状态，甚至把
 *    自己身上的 sandbox 属性摘掉。此时"代理"只是一种礼貌约定，不是安全边界。
 * 2. 活动内容以 **`iframe src` 挂载**，不是取 HTML 文本再注入。注入会让 HTML
 *    跑在宿主源里，隔离彻底失效，而且相对资源路径全断。
 * 3. 未引入桥接脚本时**明确提示**，而不是留一片空白 —— 这比任何文档都管用。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { CONTENT_BASE } from '@/api/client'
import { getPublicEvent } from '@/api/events'
import { BridgeHost } from '@/bridge/host'
import { getClientId } from '@/bridge/clientId'
import { SANDBOX_TOKENS } from '@/bridge/sandbox'
import { useConfirm, type ConfirmOptions } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import { contentEntryExists } from './contentProbe'
import {
  COMPLETE_HOLD_MS,
  PROGRESS_TICK_MS,
  STAGE_LABELS,
  nextProgress,
  type LoadStage,
} from './loadingProgress'
import type { IdentityDescriptor } from '@/bridge/protocol'
import { useAuthStore } from '@/stores/auth'
import type { EventPublic } from '@/types/api'

const props = defineProps<{ eventId: string }>()

const router = useRouter()
const auth = useAuthStore()
const toast = useToast()
const confirm = useConfirm()

/**
 * 活动页的确认框最多开多久。**必须明显小于桥接的 RPC 超时（30 秒）** —— 否则
 * 调用方先拿到超时错误，而对话框还杵在用户屏幕上，两边对同一件事的认知对不上。
 */
const CONFIRM_TIMEOUT_MS: ConfirmOptions['timeoutMs'] = 20_000

const iframe = ref<HTMLIFrameElement | null>(null)
const event = ref<EventPublic | null>(null)
const error = ref('')
const diagnostic = ref<'none' | 'missing-sdk' | 'version-mismatch' | 'missing-content'>('none')

// ---------------------------------------------------------------- 加载反馈

/**
 * 三个阶段：加载中、失败、结束。
 *
 * 覆盖层只在**加载中**和**失败**时存在 —— 结束后整块移除（`<Transition>` 会在
 * 淡出动画走完再卸载），而不是留在 DOM 里挡着下面。
 */
const phase = ref<'loading' | 'error' | 'done'>('loading')
const stage = ref<LoadStage>('event')
const progress = ref(0)

let progressTimer: ReturnType<typeof setInterval> | undefined
let finishTimer: ReturnType<typeof setTimeout> | undefined

const showOverlay = computed(() => phase.value !== 'done')
/** 失败时进度条没有意义，直接收掉 */
const showProgress = computed(() => phase.value === 'loading')
const overlayText = computed(() => error.value || STAGE_LABELS[stage.value])

function stopProgress(): void {
  if (progressTimer !== undefined) clearInterval(progressTimer)
  progressTimer = undefined
}

function startProgress(): void {
  stopProgress()
  progress.value = 0
  progressTimer = setInterval(() => {
    progress.value = nextProgress(progress.value)
  }, PROGRESS_TICK_MS)
}

/**
 * 结束加载：补齐到 100%，让"完成"被看见，再淡出。**幂等** ——
 * 握手可能因为 iframe 内部导航而重复触发。
 */
function finishLoading(): void {
  if (phase.value !== 'loading') return
  stopProgress()
  progress.value = 100
  finishTimer = setTimeout(() => {
    phase.value = 'done'
  }, COMPLETE_HOLD_MS)
}

function failLoading(message: string): void {
  stopProgress()
  error.value = message
  phase.value = 'error'
}

let host: BridgeHost | null = null

const frameSrc = computed(() => {
  if (!event.value) return ''
  const entry = event.value.entry_path || 'index.html'
  // 版本参数让内容更新后能立刻生效（未带 v 的请求一律要求回源校验）
  return `${CONTENT_BASE}/${event.value.id}/${entry}?v=${event.value.content_version}`
})

function identity(): IdentityDescriptor {
  const user = auth.user
  return {
    loggedIn: user !== null,
    userId: user?.id ?? null,
    displayName: user?.display_name ?? null,
    role: user?.role ?? null,
    clientId: getClientId(),
    submissionRequiresLogin: event.value?.submission_requires_login ?? false,
  }
}

/**
 * 把设计令牌下发给活动页。
 *
 * **这是"提供"而不是"要求"**：活动页的视觉语言由活动自己决定，宿主只把颜色与字体
 * 摆出来，用不用、用多少都随它。因此这里下发的是**完整的颜色集合**（含描边与阴影
 * 实色），而不是宿主当前恰好用到的那几个 —— 少给一个，想对齐的活动页就只能写死。
 *
 * 取值全部从宿主自己的计算样式里读，不在这里另抄一份：抄一份就多一处会漂移的真相。
 */
function theme(): Record<string, string> {
  const styles = getComputedStyle(document.documentElement)
  const names = [
    '--bg',
    '--panel',
    '--bone',
    '--edge',
    '--mute',
    '--dim',
    '--red',
    '--red-hi',
    '--line',
    '--shadow-ink',
    '--mono',
    '--sans',
    '--radius-control',
    '--radius-surface',
  ]
  const tokens: Record<string, string> = {}
  for (const name of names) tokens[name] = styles.getPropertyValue(name).trim()
  return tokens
}

function buildHost(): void {
  if (!iframe.value || !event.value) return

  host?.dispose()
  host = new BridgeHost({
    iframe: iframe.value,
    // 活动标识一律取自宿主自身路由，绝不采信 iframe 传值
    eventId: () => props.eventId,
    apiBase: `${window.location.origin}/api/v1`,
    contentBase: `${window.location.origin}${CONTENT_BASE}`,
    identity,
    theme,
    onNavigate: (to) => void router.push(to),
    onTitle: (title) => {
      // 没有可见的标题栏了，标题落到浏览器标签上
      if (title) document.title = title
    },
    /*
      活动页的提示统一走宿主的通知栈，而不是在沙箱里用 `alert` —— 这是协议文档
      指定的做法，也是 `allow-modals` 之外更好的那条路。

      语气与长度已经在桥接层收敛过（`BridgeHost.handleToast`），这里直接渲染。
    */
    onToast: ({ level, message }) => toast.push(level, message),

    /*
      活动页的确认框同理：渲染在宿主界面上，焦点与 Esc 由宿主自己的组件负责。

      `fromEvent` 会多出一行来源说明 —— 与通知不同，这个框会拦住用户并索要一次
      点击，而按钮文案由活动页给。见 `ConfirmHost` 的说明。

      `timeoutMs` 必须小于桥接的 RPC 超时（30 秒），否则调用方先拿到超时错误、
      而对话框还开在屏幕上。
    */
    onConfirm: (payload) =>
      confirm.ask({ ...payload, fromEvent: true, timeoutMs: CONFIRM_TIMEOUT_MS }),
    // 真的连上了：这是收起加载覆盖层的唯一正当理由
    onReady: () => finishLoading(),
    onBridgeMissing: () => {
      diagnostic.value = 'missing-sdk'
      // 诊断面板才是可操作的界面，覆盖层让开，否则会把它盖住
      finishLoading()
    },
    onVersionMismatch: () => {
      diagnostic.value = 'version-mismatch'
      finishLoading()
    },
  })
  host.start()

  // 登录态变化时向活动页下发更新
  const unsubscribe = auth.onChange(() => host?.pushSession())
  cleanups.push(unsubscribe)
}

const cleanups: Array<() => void> = []

/** 探测内容入口页是否存在。 */
async function load(): Promise<void> {
  error.value = ''
  diagnostic.value = 'none'
  stage.value = 'event'
  phase.value = 'loading'
  startProgress()

  try {
    event.value = await getPublicEvent(props.eventId)
    document.title = event.value.title

    // 活动存在但内容还没投放：直接说清楚，而不是显示一个 404 的 iframe
    stage.value = 'content'
    if (!(await contentEntryExists(frameSrc.value))) {
      diagnostic.value = 'missing-content'
      // 同样把覆盖层让开，露出"去投放内容"那个可操作的入口
      finishLoading()
    }
  } catch (caught) {
    if (caught instanceof ApiError && caught.status === 404) {
      // 未发布或不存在的活动：交给 404 页面，而不是在这里显示一个空白 iframe
      await router.replace({ name: 'not-found' })
      return
    }
    failLoading(caught instanceof ApiError ? caught.message : '加载活动失败')
  }
}

function onIframeLoad(): void {
  // iframe 开始跑了，接下来等它发出就绪消息
  stage.value = 'bridge'
  // 每次加载都重新握手：既覆盖 iframe 内部导航，也让重复就绪幂等
  host?.onIframeLoad()
}

onMounted(async () => {
  await load()
  // 失败或已经有诊断结论时不该再去挂 iframe
  if (error.value || diagnostic.value !== 'none') return

  // 等 DOM 把 iframe 渲染出来再挂宿主，而不是依赖微任务的先后顺序
  await nextTick()
  stage.value = 'page'
  buildHost()
})

onBeforeUnmount(() => {
  stopProgress()
  if (finishTimer !== undefined) clearTimeout(finishTimer)
  host?.dispose()
  for (const cleanup of cleanups) cleanup()
  cleanups.length = 0
})

watch(
  () => props.eventId,
  async () => {
    await load()
    if (error.value || diagnostic.value !== 'none') return
    await nextTick()
    stage.value = 'page'
    buildHost()
  },
)
</script>

<template>
  <div class="event">
    <!--
      刻意没有顶部栏：活动页就是活动自己的页面，宿主不该在它上面压一条自己的
      chrome。活动标识、登录态这些信息由活动页自己经 CEA.identity() 取用；
      标题落到浏览器标签（document.title）。
    -->

    <!-- 顶部假进度条。纯装饰，宽度由假进度驱动 -->
    <Transition name="fade">
      <div v-if="showProgress" class="event__progress" aria-hidden="true">
        <div class="event__progress-fill" :style="{ width: `${progress}%` }" />
      </div>
    </Transition>

    <!-- 未引入桥接脚本：给出可操作的提示，而不是空白 -->
    <div v-if="diagnostic === 'missing-sdk'" class="panel diag">
      <h2 class="diag__title">活动页没有接入桥接脚本</h2>
      <p class="mute diag__lead">
        活动内容已加载，但它在约定时间内没有发出就绪消息。活动页需要引入 SDK
        （通常由托管层自动注入，页面自己写也可以）：
      </p>
      <pre class="diag__code">&lt;script src="/sdk/v1/cea.js" id="cea-sdk"&gt;&lt;/script&gt;</pre>
      <p class="mute diag__lead">
        引入后活动页可以通过 <code>CEA.submit()</code> 提交信息，并读取
        <code>CEA.event()</code>。没有它，活动页既无法提交，也无法与宿主通讯。
      </p>
      <button class="btn btn--ghost btn--small" @click="onIframeLoad">重新检测</button>
    </div>

    <div v-else-if="diagnostic === 'missing-content'" class="panel diag">
      <h2 class="diag__title">活动还没有投放内容</h2>
      <p class="mute diag__lead">
        活动已经发布，但内容目录里没有入口页
        <code>{{ event?.entry_path || 'index.html' }}</code>。
        请在管理台的「网页内容」里上传内容包。
      </p>
      <RouterLink class="btn btn--ghost btn--small" :to="{ name: 'admin-event-detail', params: { eventId } }">
        去投放内容
      </RouterLink>
    </div>

    <div v-else-if="diagnostic === 'version-mismatch'" class="panel diag">
      <h2 class="diag__title">桥接脚本版本不匹配</h2>
      <p class="mute diag__lead">
        活动页引入的 SDK 主版本与宿主不一致。请把 <code>/sdk/v1/cea.js</code>
        的版本号改为与宿主一致后重新加载。
      </p>
    </div>

    <!--
      活动内容：沙箱 iframe，src 指向内容 URL，占满整个视口。
      sandbox 属性由 SANDBOX_TOKENS 绑定，**不含 allow-same-origin**。
      高度交给 CSS（100%），不再由 event:resize 驱动 —— 全屏下内容自己滚动，
      若还按内容高度撑开 iframe，长页面会把外层也撑出滚动条，变成双层滚动。
    -->
    <iframe
      v-if="event && diagnostic !== 'missing-content'"
      ref="iframe"
      class="event__frame"
      :src="frameSrc"
      :sandbox="SANDBOX_TOKENS.join(' ')"
      title="活动内容"
      referrerpolicy="no-referrer"
      @load="onIframeLoad"
    />

    <!--
      加载覆盖层：压在所有内容之上，居中显示当前阶段。
      用 <Transition> 而不是手动管显隐 —— 它在淡出动画走完才卸载，所以不会
      留一个透明的层挡着下面的点击。
    -->
    <Transition name="fade">
      <div
        v-if="showOverlay"
        class="event__overlay"
        :role="phase === 'error' ? 'alert' : 'status'"
        :aria-live="phase === 'error' ? 'assertive' : 'polite'"
      >
        <p class="event__overlay-text">
          {{ overlayText }}<span v-if="showProgress" class="cursor" />
        </p>
        <p v-if="phase === 'error'" class="event__overlay-hint">
          请稍后重试，或刷新页面。
        </p>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
/* 占满视口。用 dvh 而不是 vh：移动端浏览器地址栏收起/展开时 vh 不变，
   会让底部被裁掉一截。dvh 跟随实际可视高度。 */
.event {
  position: relative; /* 进度条与覆盖层相对它定位 */
  height: 100dvh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ------------------------------------------------------------------ */
/* 顶部假进度条                                                        */
/* ------------------------------------------------------------------ */

.event__progress {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: var(--red-soft);
  z-index: 30;
}

.event__progress-fill {
  height: 100%;
  background: var(--red);
  /* 宽度是跳着涨的，加个过渡让它看起来是连续推进的 */
  transition: width 180ms ease-out;
}

/* ------------------------------------------------------------------ */
/* 加载覆盖层                                                          */
/* ------------------------------------------------------------------ */

.event__overlay {
  position: absolute;
  inset: 0;
  z-index: 25;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 24px;
  /* 用页面底色而不是半透明黑：加载期间下面本来就没东西可看 */
  background: var(--bg);
  text-align: center;
}

.event__overlay-text {
  margin: 0;
  /* 用亮强调色而不是 --red：#d0202f 压在 #0b0b0d 上对比度偏低，读起来费劲 */
  color: var(--red-hi);
  font: 600 14px/1.6 var(--mono);
  letter-spacing: 0.06em;
}

.event__overlay-hint {
  margin: 0;
  color: var(--dim);
  font-size: 12.5px;
}

/* 淡入淡出。时长与设计令牌的过渡上限一致（≤200ms）。
   淡出期间不该再挡住点击 —— 虽然只有 200ms，但没必要。 */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 200ms ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.fade-leave-active {
  pointer-events: none;
}

.event__frame {
  flex: 1;
  width: 100%;
  min-height: 0; /* flex 子项默认 min-height:auto，会让 iframe 撑破容器 */
  border: 0;
  background: var(--bg);
  display: block;
}

.diag {
  margin: 20px;
  padding: 22px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: flex-start;
}

.diag__title {
  font-size: 16px;
  color: var(--red-hi);
}

.diag__lead {
  margin: 0;
  font-size: 13px;
  line-height: 1.8;
}

.diag__code {
  margin: 0;
  padding: 10px 12px;
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: var(--radius-surface);
  font-size: 13px;
  color: var(--bone);
}

code {
  font-family: var(--mono);
  color: var(--red-hi);
}
</style>
