<script setup lang="ts">
/**
 * 开发调试台 —— **只在开发构建里存在**。
 *
 * 用途：让"活动页 ↔ 宿主"的完整链路在本地就能跑，而不必先投放内容、发布活动。
 * 完整说明见 `docs/dev-harness.md`。
 *
 * ## 宿主行为用真的那一份
 *
 * 这里用的是与生产同一个 `BridgeHost`，不是另写一套调试宿主。测替身只能证明替身是对的，
 * 不能证明真宿主是对的；而两者的漂移是渐进的、不报错的，结果是本地通过而线上失败 ——
 * 恰好是这套设施要消灭的那类问题。
 *
 * "调试逻辑"因此只体现在三件事上，都不改变宿主语义：
 *
 * 1. **旁听**：宿主暴露的只读观测口子（`onMessage`），收发两个方向都看得到。
 * 2. **注入**：直接往 `iframe.contentWindow` 发宿主侧消息。从活动页看，发送方窗口
 *    就是 `window.parent`，因此 SDK 的来源校验会通过。
 * 3. **拦截**：宿主暴露的挂起口子（`shouldHold` / `onHold`）。命中的请求**不会发给
 *    后端**，等这里决定 —— 假响应，或转发。
 *
 * ## 拦截为什么必须是"挂起"而不是"抢先应答"
 *
 * 早先的做法是让请求照常发出去、面板抢先回一条伪造结果。那样做不到两件事：
 * 你看到请求时它**已经发出去了**，所以既没法"看清再决定"，"转发"也不成为一个选择
 * （无论选什么，后端都已经收到了）。挂起才有真正的选择。
 *
 * 代价与边界见 `BridgeHostOptions.shouldHold` 的说明：这是**行为**口子，生产宿主不传它。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { CONTENT_BASE } from '@/api/client'
import { BridgeHost, type BridgeToastLevel, type HeldRequest } from '@/bridge/host'
import { getClientId } from '@/bridge/clientId'
import {
  BRIDGE_ERROR,
  HOST_MESSAGE,
  IFRAME_MESSAGE,
  PROTOCOL_VERSION,
  type BridgeErrorCode,
  type Envelope,
  type IdentityDescriptor,
} from '@/bridge/protocol'
import { SANDBOX_TOKENS } from '@/bridge/sandbox'
import { useConfirm, type ConfirmOptions } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import {
  DEFAULT_DEV_EVENT_ID,
  FORGEABLE_CODES,
  decideInterception,
  emptyInterceptState,
  filterableOps,
  filteredOps,
  forgedError,
  formatArgs,
  formatLogTime,
  hostActions,
  summarize,
  type DebugEntry,
  type Direction,
  type HostAction,
} from './console'
import { describeSourceRejection, resolveDebugSource } from './source'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const confirm = useConfirm()

/** 确认框的自动结算时间，必须明显小于桥接的 RPC 超时（30 秒）。与生产宿主同一取值。 */
const CONFIRM_TIMEOUT_MS: ConfirmOptions['timeoutMs'] = 20_000

/** 日志最多留这么多条。挂久了不至于把内存吃满，但足够回看一轮交互。 */
const MAX_LOG = 400

const frame = ref<HTMLIFrameElement | null>(null)

// ---------------------------------------------------------------------------
// 来源与活动
// ---------------------------------------------------------------------------

const source = computed(() =>
  resolveDebugSource(route.query.src, window.location.origin),
)

const sourceProblem = computed(() =>
  source.value === null ? describeSourceRejection(route.query.src) : '',
)

const eventId = computed(() => {
  const raw = route.query.event
  return typeof raw === 'string' && raw.trim() !== ''
    ? raw.trim()
    : DEFAULT_DEV_EVENT_ID
})

const frameSrc = computed(() => source.value ?? '')

// ---------------------------------------------------------------------------
// 日志
// ---------------------------------------------------------------------------

const log = ref<DebugEntry[]>([])
const logBox = ref<HTMLElement | null>(null)
let seq = 0

function push(direction: Direction, type: string, summary: string): void {
  seq += 1
  log.value.push({ seq, at: Date.now(), direction, type, summary })
  if (log.value.length > MAX_LOG) {
    log.value.splice(0, log.value.length - MAX_LOG)
  }
  if (tab.value === 'log') {
    void nextTick(() => {
      if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight
    })
  }
}

function clearLog(): void {
  log.value = []
}

const DIRECTION_LABEL: Record<Direction, string> = {
  in: '活动页 →',
  out: '面板 →',
  forged: '假响应 →',
  held: '挂起',
  host: '宿主 →',
}

// ---------------------------------------------------------------------------
// 身份与主题：面板替换的是**输入**，不是宿主语义
// ---------------------------------------------------------------------------

const pretendLoggedIn = ref(false)
const pretendName = ref('调试用户')
const pretendRequiresLogin = ref(false)

function identity(): IdentityDescriptor {
  return {
    loggedIn: pretendLoggedIn.value,
    userId: pretendLoggedIn.value ? 999 : null,
    displayName: pretendLoggedIn.value ? pretendName.value : null,
    role: pretendLoggedIn.value ? 'user' : null,
    clientId: getClientId(),
    submissionRequiresLogin: pretendRequiresLogin.value,
  }
}

/**
 * 下发宿主自己的设计令牌，取值从计算样式读 —— 与生产宿主同一份逻辑，不另抄一份。
 * 这个动作的意义不在于"页面会变"（SDK 目前不处理 `hub:theme`），而在于能看见这条消息
 * 本身发得出去、形状对不对。
 */
function theme(): Record<string, string> {
  const styles = getComputedStyle(document.documentElement)
  const names = [
    '--bg', '--panel', '--bone', '--edge', '--mute', '--dim',
    '--red', '--red-hi', '--line', '--shadow-ink', '--mono', '--sans',
    '--radius-control', '--radius-surface',
  ]
  const tokens: Record<string, string> = {}
  for (const name of names) tokens[name] = styles.getPropertyValue(name).trim()
  return tokens
}

// ---------------------------------------------------------------------------
// 拦截：多选过滤 → 挂起 → 结算
// ---------------------------------------------------------------------------

const intercept = ref(emptyInterceptState())
const ops = filterableOps()
const activeFilters = computed(() => filteredOps(intercept.value.filters))

interface HeldEntry extends HeldRequest {
  at: number
}

const held = ref<HeldEntry[]>([])

/** 把一条挂起的请求结算掉：假响应，或转发给真后端。 */
function settleHeld(entry: HeldEntry, action: 'fail' | 'forward'): void {
  held.value = held.value.filter((item) => item.requestId !== entry.requestId)

  if (action === 'fail') {
    const error = forgedError(intercept.value.defaultCode)
    host?.failHeld(entry.requestId, error)
    push('forged', HOST_MESSAGE.RESULT, `op=${entry.op}  code=${error.code}  —— 假响应，未发往后端`)
    return
  }

  void host?.forwardHeld(entry.requestId)
  push('host', '转发', `op=${entry.op}  —— 转发给后端，结果用后端真实返回值`)
}

function settleAll(action: 'fail' | 'forward'): void {
  for (const entry of [...held.value]) settleHeld(entry, action)
}

// ---------------------------------------------------------------------------
// 宿主
// ---------------------------------------------------------------------------

let host: BridgeHost | null = null
const handshaken = ref(false)
const pendingCount = ref(0)
let statusTimer: ReturnType<typeof setInterval> | undefined

function buildHost(): void {
  if (!frame.value || source.value === null) return

  host?.dispose()
  handshaken.value = false
  // 旧宿主手里的挂起请求随 dispose 一起没了，界面上也别留着
  held.value = []

  const instance = new BridgeHost({
    iframe: frame.value,
    // 活动标识同样取自宿主自身路由（这里是调试台的查询参数），不采信 iframe 传值
    eventId: () => eventId.value,
    apiBase: `${window.location.origin}/api/v1`,
    contentBase: `${window.location.origin}${CONTENT_BASE}`,
    identity,
    theme,

    // 拦截口子：命中的请求**不进网络**，交给 onHold 决定
    shouldHold: (op) => decideInterception(intercept.value, op) !== null,
    onHold: (request) => {
      const action = decideInterception(intercept.value, request.op)

      // 默认应答：开着的时候不必逐条点
      if (action === 'fail') {
        const error = forgedError(intercept.value.defaultCode)
        instance.failHeld(request.requestId, error)
        push('forged', HOST_MESSAGE.RESULT, `op=${request.op}  code=${error.code}  —— 默认应答：假响应`)
        return
      }
      if (action === 'forward') {
        void instance.forwardHeld(request.requestId)
        push('host', '转发', `op=${request.op}  —— 默认应答：转发给后端`)
        return
      }

      // 没有默认应答：挂起来，等人看清内容再选
      held.value.push({ ...request, at: Date.now() })
      push('held', request.op, `已挂起，等待选择（id=${request.requestId}）`)
    },

    onNavigate: (to) => {
      // 不在这里补日志：`event:navigate` 已经被观测口子记成入站消息了。
      // 回调再记一条会让同一条消息出现两次，看起来像发了两条。
      void router.push(to)
    },
    onTitle: (title) => {
      if (title) document.title = title
    },
    onToast: ({ level, message }) => {
      toast.push(level as BridgeToastLevel, message)
    },
    onConfirm: (payload) =>
      confirm.ask({ ...payload, fromEvent: true, timeoutMs: CONFIRM_TIMEOUT_MS }),
    onReady: () => {
      handshaken.value = true
      // 同样不假装成一条消息：`hub:init` 的真实发送已经由观测口子记下了，
      // 这里再推一条同类型的记录会让人以为宿主发了两次初始化。
      push('host', '握手', '活动页就绪，宿主已下发初始化消息')
    },
    onBridgeMissing: () =>
      push('host', '诊断', '约定时间内没有收到就绪消息（SDK 没加载？）'),
    onVersionMismatch: (declared) =>
      push('host', '诊断', `协议主版本不匹配：活动页声明 ${declared}`),

    /**
     * 只读观测口子。入站那一半本来可以靠调试台自己挂 window 监听拿到，但**出站拿不到** ——
     * 结果是宿主发出的，而沙箱 iframe 不含 `allow-same-origin`，宿主对 `contentWindow`
     * 的属性访问会抛 SecurityError，包装 `postMessage` 做不到。
     */
    onMessage: (direction, envelope) => {
      push(direction === 'in' ? 'in' : 'host', envelope.type, summarize(envelope))
      if (direction === 'out') return
      if (envelope.type !== IFRAME_MESSAGE.RPC) return
      if (typeof envelope.id === 'string') lastRequestId.value = envelope.id
    },
  })

  host = instance
  instance.start()
}

/**
 * iframe 每次加载完都要重新握手。
 *
 * **走 `onIframeLoad()` 而不是重建宿主**：重建会 `dispose()` 掉旧宿主，而 dispose 只
 * 拒绝宿主**自己**的待决 promise，并不会给活动页回一条结果 —— 活动页那边正在等的
 * `CEA.event()` 于是永远不落地，表现为整页卡在"正在与宿主建立连接…"。
 * 生产宿主走的也是 `onIframeLoad()`，见 `event/EventView.vue`。
 */
function onFrameLoad(): void {
  host?.onIframeLoad()
  // 宿主把挂起的那些清掉了（换了一份文档，没人会来认领，见 BridgeHost.onIframeLoad），
  // 面板这边的队列也要跟着清，否则会留下一堆点不动的条目
  held.value = []
}

// ---------------------------------------------------------------------------
// 面板主动发出的宿主侧消息
// ---------------------------------------------------------------------------

const actions = hostActions()
const lastRequestId = ref('')

function sendRaw(type: string, payload: Record<string, unknown> = {}): void {
  const target = frame.value?.contentWindow
  if (!target) return
  const envelope: Envelope = { v: PROTOCOL_VERSION, type, payload }
  target.postMessage(envelope, '*')
  push('out', type, summarize(envelope))
}

function defaultPayload(action: HostAction): Record<string, unknown> {
  switch (action.type) {
    case HOST_MESSAGE.THEME:
      return { theme: theme() }
    case HOST_MESSAGE.RESULT:
      return { ok: true, data: { note: '面板手动发的成功结果' } }
    case HOST_MESSAGE.UPLOAD_PROGRESS:
      return { requestId: lastRequestId.value, loaded: 50, total: 100 }
    default:
      return {}
  }
}

function runAction(action: HostAction): void {
  if (action.kind === 'handshake') {
    // 走宿主真实路径：它要连带重置宿主的握手状态，自己发一条 hub:init 做不到
    host?.onIframeLoad()
    push('host', '握手', '重握手（经宿主真实路径）')
    return
  }
  if (action.kind === 'session') {
    host?.pushSession()
    push('host', '会话', '把面板设定的身份推给活动页')
    return
  }
  sendRaw(action.type, defaultPayload(action))
}

// ---------------------------------------------------------------------------
// 分页
// ---------------------------------------------------------------------------

type TabId = 'status' | 'identity' | 'actions' | 'intercept' | 'log'

const TABS: Array<{ id: TabId; label: string; icon: string }> = [
  { id: 'status', label: '状态', icon: 'M1 8h3l2-5 3 10 2-5h4' },
  { id: 'identity', label: '身份', icon: 'M8 7a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM2.5 14c0-2.5 2.5-3.5 5.5-3.5s5.5 1 5.5 3.5' },
  { id: 'actions', label: '动作', icon: 'M2 8h9M8 4l4 4-4 4' },
  { id: 'intercept', label: '拦截', icon: 'M6 3v10M10 3v10' },
  { id: 'log', label: '日志', icon: 'M3 4h10M3 8h10M3 12h6' },
]

const tab = ref<TabId>('status')

/** 工具轨上的角标。只有"拦截"会有 —— 它是唯一需要有人去做点什么的页。 */
function badge(id: TabId): number {
  return id === 'intercept' ? held.value.length : 0
}

// ---------------------------------------------------------------------------
// 生命周期
// ---------------------------------------------------------------------------

onMounted(() => {
  statusTimer = setInterval(() => {
    pendingCount.value = host?.pendingCount ?? 0
  }, 500)
  void nextTick(buildHost)
})

onBeforeUnmount(() => {
  if (statusTimer !== undefined) clearInterval(statusTimer)
  host?.dispose()
  host = null
})

// 换来源或换活动都重建宿主（iframe 也会因为 src 变化重新加载）
watch([source, eventId], () => {
  void nextTick(buildHost)
})

const reloadKey = ref(0)

/**
 * 手动重新加载。
 *
 * `reloadKey` 会让 Vue 换掉整个 iframe 元素，因此旧宿主手里那个 `contentWindow`
 * 引用也跟着失效 —— 这里必须**重建**宿主，与上面 `onFrameLoad()` 那条"不要重建"
 * 并不矛盾：那里元素没变，只是文档重新加载了。
 */
function reloadFrame(): void {
  reloadKey.value += 1
  push('host', '重载', '手动重新加载 iframe')
  void nextTick(buildHost)
}
</script>

<template>
  <div class="dev">
    <section class="dev__stage">
      <p v-if="sourceProblem" class="dev__problem">
        {{ sourceProblem }}
      </p>
      <iframe
        v-else
        :key="reloadKey"
        ref="frame"
        class="dev__frame"
        :src="frameSrc"
        :sandbox="SANDBOX_TOKENS.join(' ')"
        title="调试中的活动页"
        referrerpolicy="no-referrer"
        @load="onFrameLoad"
      />
    </section>

    <aside class="dev__panel">
      <div class="pane">
        <header class="pane__head">
          <h1>{{ TABS.find((t) => t.id === tab)?.label }}</h1>
          <p class="pane__sub">仅开发构建 · 宿主行为与生产同一份实现</p>
        </header>

        <!-- ---------------------------------------------------------- 状态 -->
        <div v-if="tab === 'status'" class="pane__body">
          <dl class="kv">
            <dt>来源</dt><dd>{{ source ?? '—' }}</dd>
            <dt>活动</dt><dd>{{ eventId }}</dd>
            <dt>握手</dt>
            <dd :class="handshaken ? 'dev-ok' : 'dev-warn'">
              {{ handshaken ? '已完成' : '等待活动页就绪…' }}
            </dd>
            <dt>待决请求</dt><dd>{{ pendingCount }}</dd>
            <dt>拦截过滤</dt>
            <dd :class="activeFilters.length > 0 ? 'dev-warn' : ''">
              {{ activeFilters.length > 0 ? activeFilters.join('、') : '未勾选任何操作' }}
            </dd>
            <dt>默认应答</dt>
            <dd :class="intercept.autoRespond ? 'dev-warn' : ''">
              {{ intercept.autoRespond ? intercept.defaultAction : '关闭（逐条选择）' }}
            </dd>
            <dt>挂起中</dt>
            <dd :class="held.length > 0 ? 'dev-warn' : ''">{{ held.length }}</dd>
          </dl>
          <button type="button" class="btn btn--ghost btn--small" @click="reloadFrame">
            重新加载 iframe
          </button>
        </div>

        <!-- ---------------------------------------------------------- 身份 -->
        <div v-else-if="tab === 'identity'" class="pane__body">
          <label class="check">
            <input v-model="pretendLoggedIn" type="checkbox">
            <span>冒充已登录</span>
          </label>
          <label class="dev-field">
            <span>显示名</span>
            <input v-model="pretendName" type="text" :disabled="!pretendLoggedIn">
          </label>
          <label class="check">
            <input v-model="pretendRequiresLogin" type="checkbox">
            <span>该活动要求登录（触发宿主的提交前短路）</span>
          </label>
          <p class="hint">
            这两项只改**下发给活动页的描述符**与短路判据。真实会话仍是浏览器的 Cookie，
            因此"冒充已登录"不会让后端认你 —— 它用来测活动页在两种身份下渲染得对不对。
          </p>
        </div>

        <!-- ---------------------------------------------------------- 动作 -->
        <div v-else-if="tab === 'actions'" class="pane__body">
          <p class="hint">
            清单由 `protocol.ts` 的宿主消息类型推导，协议里加一个就自动多一行。
          </p>
          <ul class="actions">
            <li v-for="action in actions" :key="action.type">
              <button type="button" class="btn btn--ghost btn--small" @click="runAction(action)">
                {{ action.type }}
              </button>
              <span class="hint">{{ action.hint }}</span>
            </li>
          </ul>
        </div>

        <!-- ---------------------------------------------------------- 拦截 -->
        <div v-else-if="tab === 'intercept'" class="pane__body pane__body--split">
          <section class="block">
            <h2>过滤（{{ ops.length }} 个操作，多选）</h2>
            <ul class="ops">
              <li v-for="op in ops" :key="op">
                <label class="check">
                  <input v-model="intercept.filters[op]" type="checkbox">
                  <span class="mono">{{ op }}</span>
                </label>
              </li>
            </ul>
            <p class="hint">
              只有勾中的操作会被拦。**未勾中的一律照常转发**，所以"一个都不勾"与加这个功能之前完全一样。
            </p>
          </section>

          <section class="block">
            <h2>默认应答</h2>
            <label class="check">
              <input v-model="intercept.autoRespond" type="checkbox">
              <span>开启（不必逐条选择）</span>
            </label>
            <label class="dev-field">
              <span>动作</span>
              <select v-model="intercept.defaultAction" :disabled="!intercept.autoRespond">
                <option value="fail">假响应（伪造一个错误，不发往后端）</option>
                <option value="forward">转发（发给后端，用真实返回值）</option>
              </select>
            </label>
            <label class="dev-field">
              <span>假响应用的错误码</span>
              <select v-model="intercept.defaultCode">
                <option v-for="code in FORGEABLE_CODES" :key="code" :value="code">
                  {{ code }}
                </option>
              </select>
            </label>
            <p class="hint">
              <b>关掉时每条都挂起等你选</b> —— 那一档才能先看清请求内容再决定。
            </p>
          </section>

          <section class="block block--grow">
            <h2>
              挂起中（{{ held.length }}）
              <span v-if="held.length > 1" class="block__bulk">
                <button type="button" class="btn btn--ghost btn--small" @click="settleAll('forward')">
                  全部转发
                </button>
                <button type="button" class="btn btn--ghost btn--small" @click="settleAll('fail')">
                  全部假响应
                </button>
              </span>
            </h2>

            <p v-if="held.length === 0" class="hint">
              没有挂起的请求。勾上过滤、关掉默认应答，活动页一发请求就会停在这里。
            </p>

            <ul v-else class="held">
              <li v-for="entry in held" :key="entry.requestId">
                <div class="held__head">
                  <span class="held__op mono">{{ entry.op }}</span>
                  <span class="held__id">id={{ entry.requestId }}</span>
                </div>
                <pre class="held__args">{{ formatArgs(entry.args) }}</pre>
                <div class="held__act">
                  <button type="button" class="btn btn--small" @click="settleHeld(entry, 'fail')">
                    假响应
                  </button>
                  <button type="button" class="btn btn--ghost btn--small" @click="settleHeld(entry, 'forward')">
                    转发
                  </button>
                </div>
              </li>
            </ul>
          </section>
        </div>

        <!-- ---------------------------------------------------------- 日志 -->
        <div v-else class="pane__body pane__body--split">
          <p class="hint">
            <code>活动页 →</code> 与 <code>宿主 →</code> 都是**真实收发**的消息（出站在宿主
            真正发送的那一处观测到，所以请求与应答能成对看到）。<code>假响应 →</code> 是面板
            伪造的结算，<code>挂起</code> 是拦下来还没决定。
          </p>
          <div class="block block--grow">
            <h2>
              日志
              <button type="button" class="btn btn--ghost btn--small" @click="clearLog">清空</button>
            </h2>
            <ol ref="logBox" class="log">
              <li v-for="entry in log" :key="entry.seq" :data-dir="entry.direction">
                <span class="log__at">{{ formatLogTime(entry.at) }}</span>
                <span class="log__dir">{{ DIRECTION_LABEL[entry.direction] }}</span>
                <span class="log__type">{{ entry.type }}</span>
                <span class="log__sum">{{ entry.summary }}</span>
              </li>
            </ol>
          </div>
        </div>
      </div>

      <!--
        工具轨：图标切换分页，避免整块面板一条长滚动。

        **放在最右侧**，即"iframe | 内容 | 轨"的顺序。分隔线与选中指示也跟着镜像到
        右侧边缘 —— 轨在右边时，指示条留在左边会紧贴内容区，看起来像内容区自己的边框。
      -->
      <nav class="rail" aria-label="调试台分页">
        <button
          v-for="item in TABS"
          :key="item.id"
          type="button"
          class="rail__btn"
          :class="{ 'rail__btn--on': tab === item.id }"
          :aria-pressed="tab === item.id"
          :title="item.label"
          @click="tab = item.id"
        >
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"
               stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path :d="item.icon" />
          </svg>
          <span v-if="badge(item.id) > 0" class="rail__badge">{{ badge(item.id) }}</span>
          <span class="rail__label">{{ item.label }}</span>
        </button>
      </nav>
    </aside>
  </div>
</template>

<style scoped>
.dev {
  display: flex;
  height: 100dvh;
  overflow: hidden;
  background: var(--bg);
  color: var(--bone);
}

.dev__stage {
  flex: 1;
  min-width: 0;
  display: flex;
}

.dev__frame {
  flex: 1;
  width: 100%;
  min-height: 0;
  border: 0;
  background: var(--bg);
}

.dev__problem {
  margin: auto;
  max-width: 46ch;
  padding: 20px;
  border: 1px solid var(--red);
  border-radius: var(--radius-surface);
  color: var(--red-hi);
  font-size: 13px;
  line-height: 1.8;
}

/* ---- 面板：工具轨 + 内容区 ------------------------------------------- */
.dev__panel {
  width: 460px;
  flex: none;
  display: flex;
  min-height: 0;
  border-left: 1px solid var(--line);
  background: var(--panel);
}

.rail {
  flex: none;
  width: 52px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 4px;
  /* 分隔线在**左侧**：轨在最右，它左边才是内容区 */
  border-left: 1px solid var(--line);
  background: var(--bg);
}

.rail__btn {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  padding: 7px 2px 5px;
  border: 0;
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--dim);
  cursor: pointer;
}

.rail__btn:hover { color: var(--bone); background: var(--select-soft); }

.rail__btn--on {
  color: var(--bone);
  /* 选中态用**外侧**竖条而不是整块底色：图标本身要留在视线里。
     轨在最右，所以外侧是右边 —— 与轨在左时的做法镜像。 */
  box-shadow: inset -2px 0 0 var(--red);
}

.rail__btn svg { width: 17px; height: 17px; }
.rail__label { font: 500 9.5px/1 var(--mono); letter-spacing: 0.02em; }

/* 角标：拦截是唯一"需要有人去做点什么"的页，漏掉会让活动页一直等着 */
.rail__badge {
  position: absolute;
  top: 3px;
  right: 5px;
  min-width: 14px;
  padding: 1px 3px;
  /* 用令牌而不是写死圆角：平台的界面语言是直角，令牌给的就是 0。
     这里写死一个 7px 会让它成为唯一的例外，而 tokens.spec.ts 正是在守这条。 */
  border-radius: var(--radius-control);
  background: var(--red);
  color: #fff;
  font: 700 9px/1.4 var(--mono);
  text-align: center;
}

.pane {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 12px 14px 14px;
}

.pane__head { flex: none; margin-bottom: 10px; }

.pane__head h1 {
  margin: 0;
  font: 700 14px/1.3 var(--mono);
  letter-spacing: 0.06em;
}

.pane__sub { margin: 3px 0 0; color: var(--dim); font-size: 11px; }

/* 每页自己占满剩余高度；只有内容本身需要滚动的地方才滚 */
.pane__body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.pane__body--split { gap: 12px; }

.block {
  flex: none;
  padding: 9px 11px;
  border: 1px solid var(--line);
  border-radius: var(--radius-surface);
  background: var(--bg);
}

.block--grow { flex: 1; min-height: 0; display: flex; flex-direction: column; }

.block h2 {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 0 0 7px;
  font: 600 11.5px/1.4 var(--mono);
  letter-spacing: 0.06em;
  color: var(--mute);
}

.block__bulk { display: flex; gap: 6px; }

.kv {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 2px 10px;
  margin: 0 0 10px;
  font: 500 11.5px/1.7 var(--mono);
}

.kv dt { color: var(--dim); }
.kv dd { margin: 0; overflow-wrap: anywhere; }
/*
  刻意**不用** .ok / .warn 这类短名字：它们是全局样式表里的类（`.ok` 带边框），
  scoped 只给选择器加作用域、不会阻止全局规则命中同一个元素 —— 于是我自己设的颜色
  生效了，全局那条边框也照样生效，屏幕上多出一个没人要的方框。
*/
.kv .dev-ok { color: var(--bone); }
.kv .dev-warn { color: var(--red-hi); }

.dev-field { display: flex; flex-direction: column; gap: 4px; margin-bottom: 8px; }
.dev-field > span { font: 600 11px/1 var(--mono); color: var(--dim); }

.dev-field input,
.dev-field select {
  padding: 6px 8px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
  color: var(--bone);
  font: 500 12px/1.4 var(--mono);
}

.check {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-bottom: 6px;
  font-size: 12px;
  line-height: 1.5;
  cursor: pointer;
}

.check input { margin: 2px 0 0; accent-color: var(--red); flex: none; }

.hint {
  margin: 6px 0 0;
  color: var(--dim);
  font-size: 11px;
  line-height: 1.65;
}

.hint code,
.mono { font-family: var(--mono); color: var(--mute); }

.actions {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.actions li { display: flex; flex-direction: column; gap: 2px; }

.ops {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 8px;
}

.ops .check { margin-bottom: 2px; }
.ops .mono { font-size: 11px; }

/* ---- 挂起队列 ---- */
.held {
  flex: 1;
  min-height: 0;
  margin: 0;
  padding: 0;
  overflow-y: auto;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.held li {
  padding: 8px 9px;
  border: 1px solid var(--red);
  border-radius: var(--radius-control);
  background: var(--panel);
}

.held__head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.held__op { color: var(--bone); font-size: 12px; }
.held__id { color: var(--dim); font: 500 10.5px/1.4 var(--mono); }

.held__args {
  margin: 6px 0;
  padding: 6px 8px;
  max-height: 150px;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
  background: var(--bg);
  color: var(--mute);
  font: 500 10.5px/1.5 var(--mono);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.held__act { display: flex; gap: 6px; }

/*
  按钮不自己定义样式，直接用平台既有的 `.btn` 系列 —— 调试台是开发工具，
  但它出现在同一个界面语言里，没必要长成另一套。同理 `.mono` 也是全局工具类。
*/

.log {
  flex: 1;
  min-height: 0;
  margin: 0;
  padding: 6px 8px;
  overflow-y: auto;
  list-style: none;
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
  background: var(--panel);
  font: 500 11px/1.6 var(--mono);
}

.log li {
  display: grid;
  /* 时刻 / 方向 / 类型 / 摘要。时刻单占一列，"应答是不是超时之后才到的"要能一眼看出来 */
  grid-template-columns: 52px 58px minmax(80px, auto) 1fr;
  gap: 6px;
  padding: 2px 0;
  border-bottom: 1px solid var(--line);
}

.log li:last-child { border-bottom: 0; }
.log__at { color: var(--dim); }
.log__dir { color: var(--dim); }
.log__type { color: var(--bone); overflow-wrap: anywhere; }
.log__sum { color: var(--mute); overflow-wrap: anywhere; }

/* 假响应与真实往返在视觉上必须能分开 —— 否则"这次是伪造的"会被忽略 */
.log li[data-dir='forged'] .log__dir,
.log li[data-dir='forged'] .log__sum { color: var(--red-hi); }
.log li[data-dir='held'] .log__dir,
.log li[data-dir='held'] .log__sum { color: var(--red-hi); }
.log li[data-dir='host'] .log__dir { color: var(--mute); font-style: italic; }
</style>
