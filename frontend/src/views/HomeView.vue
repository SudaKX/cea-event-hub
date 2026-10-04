<script setup lang="ts">
/**
 * 首页：这个平台的门面。
 *
 * 版式取自社团既有的海报页面（posters/index.html）：立绘在左、文字在右，标题带
 * 一个闪烁的方块光标。那张页面的主体是一个"输入路径跳转"的表单（它是静态站点
 * 的目录页），这里换成**进行中的活动列表** —— 同样是在"前往某处"，只是目标由
 * 平台自己列出来，不需要访客记住路径。
 *
 * 公开页面：**不需要登录**。管理员入口只是一个次要链接，未登录时它会自然地把人
 * 引到登录页（路由守卫负责）。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { listPublicEvents } from '@/api/events'
import elliaUrl from '@/assets/ellia.png'
import Select, { type SelectOption } from '@/components/ui/Select.vue'
import { useAuthStore } from '@/stores/auth'
import type { EventPublic } from '@/types/api'

const router = useRouter()
const auth = useAuthStore()

const events = ref<EventPublic[]>([])
const loading = ref(true)
const error = ref('')

/**
 * 首页卡片区只放**置顶**的活动。
 *
 * 这是三档可见性里第 2 档的全部含义：「公开」（1）已经出现在列表端点与上面的标识
 * 补全里，卡片区负责的是"突出"，不是"能不能被找到"。
 */
const pinnedEvents = computed(() => events.value.filter((event) => event.pinned))

/**
 * "按标识前往"的候选项。
 *
 * 用的是**完整的**公开列表（含未置顶的），不是卡片区那份。补全的作用是"目录里有
 * 哪些"，把它砍成只剩置顶的就本末倒置了。
 *
 * 它当然也不含不可见的活动：那类活动不进任何公开面，拿到链接的人直接用链接打开
 * 即可（见后端的 EventVisibility）。
 */
const jumpOptions = computed<SelectOption[]>(() =>
  events.value.map((event) => ({ value: event.id, label: `${event.id} — ${event.title}` })),
)

function jump(eventId: string): void {
  if (!eventId) return
  void router.push({ name: 'event', params: { eventId } })
}

/** 提交是否还开着。只用于给访客一个"能不能交"的提示，不参与任何判定。 */
function submissionsOpen(event: EventPublic): boolean {
  const now = Date.now()
  if (event.submissions_open_at && now < Date.parse(event.submissions_open_at)) return false
  if (event.submissions_close_at && now > Date.parse(event.submissions_close_at)) return false
  if (event.quota.limit !== null && event.quota.remaining === 0) return false
  return true
}

onMounted(async () => {
  try {
    events.value = await listPublicEvents()
    error.value = ''
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '加载活动失败'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <main class="home">
    <img
      class="home__art"
      :src="elliaUrl"
      width="182"
      height="270"
      alt="Ellia：黑发红瞳、披着红衬里外套的角色立绘"
    />

    <div class="home__col">
      <h1 class="home__title">
        CEA <em>活动平台</em><span class="cursor" aria-hidden="true"></span>
      </h1>
      <p class="home__lead">
        社团活动的入口。挑一个进去看看，或者到管理台创建新的活动。
      </p>

      <!--
        按标识直达。用现成的 Select（可搜索）而不是自己搓一个 combobox：它已经
        是"输入即过滤 + 全键盘操作"，再写一个只会多一份键盘处理要维护。
      -->
      <div v-if="events.length > 0" class="home__jump">
        <Select
          :model-value="''"
          :options="jumpOptions"
          placeholder="或直接输入活动标识 / 标题"
          searchable
          aria-label="按标识前往活动"
          @update:model-value="jump"
        />
      </div>

      <section class="home__section">
        <h2 class="home__label">置顶活动</h2>

        <p v-if="loading" class="home__note dim">加载中…</p>
        <p v-else-if="error" class="home__note home__note--bad" role="alert">{{ error }}</p>
        <p v-else-if="pinnedEvents.length === 0" class="home__note dim">
          当前没有置顶的活动。用上面的输入框按标识前往，或到管理台看看全部活动。
        </p>

        <ul v-else class="events">
          <li v-for="event in pinnedEvents" :key="event.id" class="events__item">
            <RouterLink class="events__link" :to="{ name: 'event', params: { eventId: event.id } }">
              <span class="events__head">
                <span class="events__title">{{ event.title }}</span>
                <span class="events__go" aria-hidden="true">→</span>
              </span>
              <span class="events__meta">
                <span class="mono dim">{{ event.id }}</span>
                <span v-if="!submissionsOpen(event)" class="tag tag--ignored">已截止</span>
                <span v-else-if="event.quota.limit !== null" class="tag">
                  名额 {{ event.quota.used }} / {{ event.quota.limit }}
                </span>
              </span>
              <span v-if="event.summary" class="events__summary">{{ event.summary }}</span>
            </RouterLink>
          </li>
        </ul>
      </section>

      <p class="home__foot">
        <!--
          未登录时给**登录**入口："管理台"那个链接虽然也会把人引到登录页，但那要
          先点进去才发现 —— 首页是门面，得让人一眼知道自己能做什么。

          管理台入口对"已登录的普通用户"隐藏（点进去只会被守卫弹回来），但**对
          管理员仍然显示** —— 否则管理员在自己的首页上找不到入口。
        -->
        <template v-if="!auth.isLoggedIn">
          <RouterLink class="home__link" :to="{ name: 'login' }">登录</RouterLink>
          <span class="dim" aria-hidden="true">·</span>
          <RouterLink class="home__link" :to="{ name: 'register' }">注册</RouterLink>
          <span class="dim" aria-hidden="true">·</span>
          <RouterLink class="home__link" :to="{ name: 'admin-events' }">管理台</RouterLink>
        </template>
        <template v-else>
          <span class="dim">
            已登录：<span class="mono">{{ auth.user?.display_name }}</span>
          </span>
          <template v-if="auth.isAdmin">
            <span class="dim" aria-hidden="true">·</span>
            <RouterLink class="home__link" :to="{ name: 'admin-events' }">管理台</RouterLink>
          </template>
        </template>
      </p>
    </div>
  </main>
</template>

<style scoped>
/*
  居中布局复用 `.auth` 的同款做法：`#app` 是 100% 高，所以这里用 min-height 撑满。
  窄屏时立绘与文字改用纵向排列（见下面的媒体查询）。
*/
.home {
  min-height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: clamp(18px, 3.2vw, 38px);
  padding: 40px 28px;
}

.home__art {
  flex: none;
  display: block;
  width: clamp(96px, 12vw, 150px);
  height: auto;
  /* 立绘是透明底的，给一点投影让它从暗底上"站"起来 */
  filter: drop-shadow(0 10px 20px rgba(0, 0, 0, 0.65));
}

.home__col {
  min-width: 0;
  width: clamp(260px, 42vw, 460px);
}

.home__title {
  margin: 0;
  font: 700 clamp(24px, 4vw, 40px) / 1.15 var(--mono);
  letter-spacing: 0.06em;
}

.home__title em {
  font-style: normal;
  color: var(--red-hi);
}

/* 海报页面的标志性元素：标题后面一个闪烁的方块光标 */
.cursor {
  display: inline-block;
  width: 0.5em;
  height: 1em;
  margin-left: 0.16em;
  vertical-align: -0.12em;
  background: var(--red);
  animation: blink 1.1s steps(1, end) infinite;
}

@keyframes blink {
  0%,
  50% {
    opacity: 1;
  }
  51%,
  100% {
    opacity: 0;
  }
}

.home__lead {
  margin: 14px 0 0;
  color: var(--mute);
  font-size: clamp(13px, 1.25vw, 15px);
  line-height: 1.8;
}

.home__jump {
  margin-top: 16px;
}

.home__section {
  margin-top: 22px;
}

.home__label {
  margin: 0 0 8px;
  font: 600 11px/1 var(--mono);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--dim);
}

.home__note {
  margin: 0;
  padding: 12px 14px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius-surface);
  font-size: 13px;
}

.home__note--bad {
  border-color: var(--red);
  color: var(--red-hi);
}

.events {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.events__item {
  min-width: 0;
}

.events__link {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 14px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius-surface);
  color: var(--bone);
  transition:
    border-color var(--transition-fast),
    background var(--transition-fast);
}

.events__link:hover {
  border-color: var(--red-hi);
  text-decoration: none;
}

.events__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.events__title {
  font: 600 14px/1.4 var(--sans);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.events__go {
  flex: none;
  color: var(--red-hi);
}

.events__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11.5px;
}

.events__summary {
  color: var(--mute);
  font-size: 12.5px;
  line-height: 1.7;
  /* 简介长度不受控，最多两行 */
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.home__foot {
  margin: 22px 0 0;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 12.5px;
}

.home__link {
  font-size: 12.5px;
}

@media (max-width: 560px) {
  .home {
    flex-direction: column;
    text-align: center;
  }

  .home__col {
    width: min(84vw, 420px);
  }

  .events__link {
    text-align: left;
  }
}

/* 尊重系统的"减少动效"偏好 */
@media (prefers-reduced-motion: reduce) {
  .cursor {
    animation: none;
  }
}
</style>
