<script setup lang="ts">
/** 活动列表与创建。 */
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { ApiError } from '@/api/client'
import { createEvent, listAdminEvents } from '@/api/events'
import Checkbox from '@/components/ui/Checkbox.vue'
import type { EventAdmin } from '@/types/api'

const events = ref<EventAdmin[]>([])
const loading = ref(true)
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
  error.value = ''
  try {
    await createEvent({
      id: draft.value.id.trim(),
      title: draft.value.title.trim(),
      summary: draft.value.summary.trim() || undefined,
      submission_requires_login: draft.value.submission_requires_login,
      // 空串表示"不限额"，不传该字段即走默认
      max_submissions: draft.value.max_submissions === '' ? undefined : Number(draft.value.max_submissions),
    })
    showCreate.value = false
    draft.value = {
      id: '',
      title: '',
      summary: '',
      submission_requires_login: false,
      max_submissions: '',
    }
    await load()
  } catch (caught) {
    if (caught instanceof ApiError) {
      error.value = caught.message
      fieldErrors.value = caught.fields ?? {}
    } else {
      error.value = '创建失败'
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
      </div>

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
            <th>提交</th>
            <th>配额</th>
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
            <td class="num">{{ event.submission_requires_login ? '需登录' : '可匿名' }}</td>
            <td class="num">{{ quotaText(event) }}</td>
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
