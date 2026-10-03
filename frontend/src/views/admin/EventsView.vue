<script setup lang="ts">
/** 活动列表与创建。 */
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { ApiError } from '@/api/client'
import { createEvent, listAdminEvents } from '@/api/events'
import Checkbox from '@/components/ui/Checkbox.vue'
import Select from '@/components/ui/Select.vue'
import { useToast } from '@/composables/useToast'
import {
  EVENT_VISIBILITY,
  EVENT_VISIBILITY_OPTIONS,
  parseVisibility,
  visibilityLabel,
  visibilityTone,
} from '@/domain/event'
import type { EventAdmin } from '@/types/api'

const toast = useToast()

const events = ref<EventAdmin[]>([])
const loading = ref(true)
/** 只留**加载失败**；创建的结果走通知（见 useToast 里的分工表） */
const error = ref('')
const showCreate = ref(false)
const busy = ref(false)
const fieldErrors = ref<Record<string, string>>({})

const draft = ref({
  id: '',
  title: '',
  summary: '',
  submission_requires_login: false,
  max_submissions: '' as string,
  /** 单个提交者最多几份；留空 = 不限 */
  max_per_submitter: '' as string,
  // 默认公开：与加这个字段之前的行为一致
  visibility: String(EVENT_VISIBILITY.PUBLIC),
})

async function load(): Promise<void> {
  loading.value = true
  try {
    events.value = await listAdminEvents()
    error.value = ''
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function onCreate(): Promise<void> {
  busy.value = true
  fieldErrors.value = {}
  const created = draft.value.id.trim()
  try {
    await createEvent({
      id: created,
      title: draft.value.title.trim(),
      summary: draft.value.summary.trim() || undefined,
      submission_requires_login: draft.value.submission_requires_login,
      // 空串表示"不限额"，不传该字段即走默认
      max_submissions: draft.value.max_submissions === '' ? undefined : Number(draft.value.max_submissions),
      // 同理，空串 = 不限。**不能写 Number('')** —— 那是 0，而下限是 1
      max_per_submitter:
        draft.value.max_per_submitter === ''
          ? undefined
          : Number(draft.value.max_per_submitter),
      visibility: parseVisibility(draft.value.visibility),
    })
    showCreate.value = false
    draft.value = {
      id: '',
      title: '',
      summary: '',
      submission_requires_login: false,
      max_submissions: '',
      max_per_submitter: '',
      visibility: String(EVENT_VISIBILITY.PUBLIC),
    }
    await load()
    toast.ok(`活动 ${created} 已创建，当前为草稿`)
  } catch (caught) {
    /*
      字段级错误留在表单里（用户正看着输入框，要指出的是**哪一个**字段），
      其余走通知。
    */
    if (caught instanceof ApiError) {
      fieldErrors.value = caught.fields ?? {}
      if (Object.keys(fieldErrors.value).length === 0) toast.fail(caught.message)
    } else {
      toast.fail('创建失败')
    }
  } finally {
    busy.value = false
  }
}

function quotaText(event: EventAdmin): string {
  if (event.quota.limit === null) return '不限'
  return `${event.quota.used} / ${event.quota.limit}`
}

onMounted(load)
</script>

<template>
  <section class="stack">
    <header class="head">
      <div>
        <h1 class="head__title">活动</h1>
        <p class="mute head__lead">新建的活动是草稿状态，发布后才会出现在公开页面。</p>
      </div>
      <button class="btn btn--primary" type="button" @click="showCreate = !showCreate">
        {{ showCreate ? '取消' : '新建活动' }}
      </button>
    </header>

    <p v-if="error" class="alert" role="alert">{{ error }}</p>

    <form v-if="showCreate" class="panel create" @submit.prevent="onCreate">
      <div class="create__grid">
        <label class="field">
          <span class="field__label">活动标识</span>
          <input v-model="draft.id" placeholder="2026spring" required />
          <span class="field__hint dim">
            小写字母、数字、- 与 _。它同时是 URL、内容目录名与数据目录名，**创建后不可修改**。
          </span>
          <span v-if="fieldErrors.id" class="field__error">{{ fieldErrors.id }}</span>
        </label>

        <label class="field">
          <span class="field__label">标题</span>
          <input v-model="draft.title" required />
          <span v-if="fieldErrors.title" class="field__error">{{ fieldErrors.title }}</span>
        </label>
      </div>

      <label class="field">
        <span class="field__label">简介<span class="dim">（可选）</span></span>
        <textarea v-model="draft.summary" />
      </label>

      <label class="field">
        <span class="field__label">可见性</span>
        <Select
          :model-value="draft.visibility"
          :options="EVENT_VISIBILITY_OPTIONS"
          aria-label="可见性"
          @update:model-value="(v) => (draft.visibility = v)"
        />
        <span class="field__hint dim">
          不公开的活动**不出现在任何公开面**，但知道标识的人仍可直接用链接打开。
        </span>
      </label>

      <div class="create__grid">
        <!--
          复选框自成一行控件。裸的 <input type="checkbox"> 会被 .field input 的
          width:100% 撑满整行，把标签文字挤到只剩几像素、疯狂折行。
        -->
        <div class="field">
          <span class="field__label">提交</span>
          <div class="toggle-row">
            <Checkbox v-model="draft.submission_requires_login" label="提交需要登录" />
            <span
              class="toggle-row__text"
              @click="draft.submission_requires_login = !draft.submission_requires_login"
            >
              提交需要登录
            </span>
          </div>
        </div>

        <label class="field">
          <span class="field__label">条数上限<span class="dim">（留空取默认）</span></span>
          <input v-model="draft.max_submissions" type="number" min="0" placeholder="4096" />
        </label>

        <label class="field">
          <span class="field__label">每人最多<span class="dim">（留空不限）</span></span>
          <input v-model="draft.max_per_submitter" type="number" min="1" placeholder="1" />
        </label>
      </div>

      <!-- 限制的边界要写在界面上，否则管理员会以为它是硬限制 -->
      <p class="dim note">
        「每人最多」对<strong>匿名</strong>活动只能防误操作：匿名提交者的身份由
        客户端自报，换一个浏览器即可绕过。要真正限制，请勾选上面的"提交需要登录"。
      </p>

      <button class="btn btn--primary" type="submit" :disabled="busy">
        {{ busy ? '创建中…' : '创建' }}
      </button>
    </form>

    <div class="panel">
      <p v-if="loading" class="empty">加载中…</p>
      <p v-else-if="events.length === 0" class="empty">还没有活动。</p>
      <table v-else class="table">
        <thead>
          <tr>
            <th>标识</th>
            <th>标题</th>
            <th>状态</th>
            <th>可见性</th>
            <th>提交</th>
            <th>配额</th>
            <th>每人</th>
            <th>内容版本</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="event in events" :key="event.id">
            <td class="num">
              <RouterLink :to="{ name: 'admin-event-detail', params: { eventId: event.id } }">
                {{ event.id }}
              </RouterLink>
            </td>
            <td>{{ event.title }}</td>
            <td><span class="tag" :class="`tag--${event.status}`">{{ event.status }}</span></td>
            <td>
              <span class="tag" :class="`tag--${visibilityTone(event.visibility)}`">
                {{ visibilityLabel(event.visibility) }}
              </span>
            </td>
            <td class="num">{{ event.submission_requires_login ? '需登录' : '可匿名' }}</td>
            <td class="num">{{ quotaText(event) }}</td>
            <td class="num">{{ event.max_per_submitter ?? '不限' }}</td>
            <td class="num">v{{ event.content_version }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.head__title {
  font-size: 20px;
}

.note {
  margin: 0 0 14px;
  font-size: 12.5px;
  line-height: 1.8;
}

.note strong {
  color: var(--bone);
}

.head__lead {
  margin: 6px 0 0;
  font-size: 13px;
}

.create {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.create__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 14px;
}

/* 复选框那一行要占满输入框的高度，两列网格才对得齐 */
.toggle-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: var(--control-height);
}

.toggle-row__text {
  font-size: 13px;
  cursor: pointer;
  user-select: none;
}
</style>
