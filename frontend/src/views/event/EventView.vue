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
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { CONTENT_BASE } from '@/api/client'
import { getPublicEvent } from '@/api/events'
import { BridgeHost } from '@/bridge/host'
import { getClientId } from '@/bridge/clientId'
import { contentEntryExists } from './contentProbe'
import type { IdentityDescriptor } from '@/bridge/protocol'
import { useAuthStore } from '@/stores/auth'
import type { EventPublic } from '@/types/api'

const props = defineProps<{ eventId: string }>()

const router = useRouter()
const auth = useAuthStore()

const iframe = ref<HTMLIFrameElement | null>(null)
const event = ref<EventPublic | null>(null)
const loading = ref(true)
const error = ref('')
const diagnostic = ref<'none' | 'missing-sdk' | 'version-mismatch' | 'missing-content'>('none')

let host: BridgeHost | null = null

/** 沙箱令牌集合。刻意**不含** allow-same-origin，见文件头说明。 */
const SANDBOX_TOKENS = ['allow-scripts', 'allow-forms', 'allow-modals', 'allow-popups']

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

/** 把设计令牌下发给活动页，使活动内容与宿主观感一致。 */
function theme(): Record<string, string> {
  const styles = getComputedStyle(document.documentElement)
  const names = [
    '--bg',
    '--panel',
    '--bone',
    '--mute',
    '--dim',
    '--red',
    '--red-hi',
    '--line',
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
    onToast: (payload) => {
      // 活动页的提示统一走宿主，避免在沙箱里用 alert
      console.info('[event toast]', payload.level ?? 'info', payload.message)
    },
    onBridgeMissing: () => {
      diagnostic.value = 'missing-sdk'
    },
    onVersionMismatch: () => {
      diagnostic.value = 'version-mismatch'
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
  loading.value = true
  error.value = ''
  diagnostic.value = 'none'
  try {
    event.value = await getPublicEvent(props.eventId)
    document.title = event.value.title

    // 活动存在但内容还没投放：直接说清楚，而不是显示一个 404 的 iframe
    if (!(await contentEntryExists(frameSrc.value))) {
      diagnostic.value = 'missing-content'
    }
  } catch (caught) {
    if (caught instanceof ApiError && caught.status === 404) {
      // 未发布或不存在的活动：交给 404 页面，而不是在这里显示一个空白 iframe
      await router.replace({ name: 'not-found' })
      return
    }
    error.value = caught instanceof ApiError ? caught.message : '加载活动失败'
  } finally {
    loading.value = false
  }
}

function onIframeLoad(): void {
  // 每次加载都重新握手：既覆盖 iframe 内部导航，也让重复就绪幂等
  host?.onIframeLoad()
}

onMounted(async () => {
  await load()
  buildHost()
})

onBeforeUnmount(() => {
  host?.dispose()
  for (const cleanup of cleanups) cleanup()
  cleanups.length = 0
})

watch(
  () => props.eventId,
  async () => {
    await load()
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
    <p v-if="error" class="alert event__alert" role="alert">{{ error }}</p>
    <p v-else-if="loading" class="empty">加载中…</p>

    <!-- 未引入桥接脚本：给出可操作的提示，而不是空白 -->
    <div v-else-if="diagnostic === 'missing-sdk'" class="panel diag">
      <h2 class="diag__title">活动页没有接入桥接脚本</h2>
      <p class="mute diag__lead">
        活动内容已加载，但它在约定时间内没有发出就绪消息。活动页需要在
        <code>&lt;head&gt;</code> 或 <code>&lt;body&gt;</code> 中引入 SDK：
      </p>
      <pre class="diag__code">&lt;script src="/sdk/v1/cea.js"&gt;&lt;/script&gt;</pre>
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
  </div>
</template>

<style scoped>
/* 占满视口。用 dvh 而不是 vh：移动端浏览器地址栏收起/展开时 vh 不变，
   会让底部被裁掉一截。dvh 跟随实际可视高度。 */
.event {
  height: 100dvh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.event__alert {
  margin: 16px 20px;
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
