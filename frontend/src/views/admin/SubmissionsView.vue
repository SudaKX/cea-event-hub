<script setup lang="ts">
/**
 * 提交审核：先选活动，再按分类/状态筛选。
 *
 * 分类标签是自由字段（不做语义校验），因此筛选项由**实际出现过的值**推导，
 * 而不是预设一份清单 —— 平台并不知道各活动会用什么标签。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { ApiError } from '@/api/client'
import { listAdminEvents } from '@/api/events'
import {
  attachmentUrl,
  deleteSubmission,
  deleteSubmissions,
  listEventSubmissions,
  reviewSubmission,
} from '@/api/submissions'
import type { EventAdmin, Submission } from '@/types/api'

const events = ref<EventAdmin[]>([])
const eventId = ref('')
const kind = ref('')
const status = ref('')
const page = ref(1)
const pageSize = 20

const submissions = ref<Submission[]>([])
const total = ref(0)
const selected = ref<Set<number>>(new Set())
const error = ref('')
const loading = ref(false)

const kinds = computed(() => [...new Set(submissions.value.map((s) => s.kind))].sort())
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

async function loadEvents(): Promise<void> {
  try {
    events.value = await listAdminEvents()
    const first = events.value[0]
    if (!eventId.value && first) eventId.value = first.id
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '加载活动失败'
  }
}

async function loadSubmissions(): Promise<void> {
  if (!eventId.value) return
  loading.value = true
  try {
    const result = await listEventSubmissions(eventId.value, {
      kind: kind.value || undefined,
      status: status.value || undefined,
      page: page.value,
      page_size: pageSize,
    })
    submissions.value = result.submissions
    total.value = result.total
    selected.value = new Set()
    error.value = ''
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '加载提交失败'
  } finally {
    loading.value = false
  }
}

async function onReview(submission: Submission, next: string): Promise<void> {
  try {
    await reviewSubmission(submission.id, next)
    await loadSubmissions()
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '操作失败'
  }
}

async function onDelete(submission: Submission): Promise<void> {
  if (!window.confirm('删除这条提交？名额会立即释放。')) return
  try {
    await deleteSubmission(submission.id)
    await loadSubmissions()
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '删除失败'
  }
}

async function onBatchDelete(): Promise<void> {
  const ids = [...selected.value]
  if (ids.length === 0) return
  if (!window.confirm(`删除选中的 ${ids.length} 条提交？名额会立即释放。`)) return
  try {
    await deleteSubmissions(ids)
    await loadSubmissions()
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '批量删除失败'
  }
}

function toggle(id: number): void {
  const next = new Set(selected.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selected.value = next
}

watch([eventId, kind, status], () => {
  page.value = 1
  void loadSubmissions()
})
watch(page, () => void loadSubmissions())

onMounted(async () => {
  await loadEvents()
  await loadSubmissions()
})
</script>

<template>
  <section class="stack">
    <header class="head">
      <div>
        <h1 class="head__title">提交</h1>
        <p class="mute head__lead">审核与清理。删除会立即释放该活动占用的名额。</p>
      </div>
      <button
        class="btn btn--danger btn--small"
        type="button"
        :disabled="selected.size === 0"
        @click="onBatchDelete"
      >
        删除选中（{{ selected.size }}）
      </button>
    </header>

    <!--
      这三个动作容易被当成同一件事，实际差别很大，尤其"拒绝"并不释放名额 ——
      满额活动上如果只拒绝不删除，活动仍然是满的。
    -->
    <dl class="legend panel">
      <div class="legend__item">
        <dt class="mono">接受 / 拒绝</dt>
        <dd>只改审核状态，供你自己归档。<strong>不删除数据，也不释放名额。</strong></dd>
      </div>
      <div class="legend__item">
        <dt class="mono">删除</dt>
        <dd>真正移除该条提交及其附件，并在同一事务里<strong>释放一个名额</strong>。</dd>
      </div>
      <div class="legend__item">
        <dt class="mono">状态</dt>
        <dd>
          <span class="tag">received</span> 新提交的初始状态 ·
          <span class="tag tag--reviewing">reviewing</span> 正在看 ·
          <span class="tag tag--accepted">accepted</span> 通过 ·
          <span class="tag tag--rejected">rejected</span> 不通过
        </dd>
      </div>
    </dl>

    <p v-if="error" class="alert" role="alert">{{ error }}</p>

    <div class="panel filters">
      <label class="field">
        <span class="field__label">活动</span>
        <select v-model="eventId">
          <option v-for="item in events" :key="item.id" :value="item.id">
            {{ item.id }} — {{ item.title }}
          </option>
        </select>
      </label>

      <label class="field">
        <span class="field__label">分类</span>
        <select v-model="kind">
          <option value="">全部</option>
          <option v-for="value in kinds" :key="value" :value="value">{{ value }}</option>
        </select>
      </label>

      <label class="field">
        <span class="field__label">状态</span>
        <select v-model="status">
          <option value="">全部</option>
          <option value="received">received</option>
          <option value="reviewing">reviewing</option>
          <option value="accepted">accepted</option>
          <option value="rejected">rejected</option>
        </select>
      </label>
    </div>

    <div class="panel">
      <p v-if="loading" class="empty">加载中…</p>
      <p v-else-if="events.length === 0" class="empty">还没有活动。</p>
      <p v-else-if="submissions.length === 0" class="empty">没有符合条件的提交。</p>
      <table v-else class="table">
        <thead>
          <tr>
            <th />
            <th>#</th>
            <th>提交者</th>
            <th>分类</th>
            <th>内容</th>
            <th>附件</th>
            <th>状态</th>
            <th>时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in submissions" :key="item.id">
            <td>
              <input
                type="checkbox"
                :checked="selected.has(item.id)"
                @change="toggle(item.id)"
              />
            </td>
            <td class="num">{{ item.id }}</td>
            <td class="num">
              {{ item.submitter }}
              <span v-if="!item.from_authenticated_user" class="tag">匿名</span>
            </td>
            <td class="num">{{ item.kind }}</td>
            <td><code class="payload">{{ JSON.stringify(item.payload) }}</code></td>
            <td>
              <a
                v-for="file in item.files"
                :key="file.id"
                class="mono file-link"
                :href="attachmentUrl(item.id, file.id)"
              >
                {{ file.original_name }}
              </a>
              <span v-if="item.files.length === 0" class="dim">—</span>
            </td>
            <td><span class="tag" :class="`tag--${item.status}`">{{ item.status }}</span></td>
            <td class="num dim">{{ new Date(item.created_at).toLocaleString('zh-CN') }}</td>
            <td class="actions">
              <button
                class="btn btn--ghost btn--small"
                title="标记为通过。只改状态，不删数据、不释放名额。"
                @click="onReview(item, 'accepted')"
              >
                接受
              </button>
              <button
                class="btn btn--ghost btn--small"
                title="标记为不通过。提交仍会留在列表里，仍占用名额；要腾出名额请用「删除」。"
                @click="onReview(item, 'rejected')"
              >
                拒绝
              </button>
              <button
                class="btn btn--danger btn--small"
                title="删除该条提交及其附件，并释放一个名额。不可撤销。"
                @click="onDelete(item)"
              >
                删除
              </button>
            </td>
          </tr>
        </tbody>
      </table>

      <div v-if="pageCount > 1" class="pager">
        <button class="btn btn--ghost btn--small" :disabled="page <= 1" @click="page -= 1">
          上一页
        </button>
        <span class="num dim">{{ page }} / {{ pageCount }}</span>
        <button
          class="btn btn--ghost btn--small"
          :disabled="page >= pageCount"
          @click="page += 1"
        >
          下一页
        </button>
      </div>
    </div>

    <p class="mute foot">
      需要按活动查看策略与内容？<RouterLink to="/admin/events">去活动页</RouterLink>
    </p>
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

.filters {
  padding: 16px 18px;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px;
}

.legend {
  margin: 0;
  padding: 14px 18px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 12.5px;
}

.legend__item {
  display: flex;
  gap: 12px;
  align-items: baseline;
}

.legend__item dt {
  flex: none;
  width: 96px;
  color: var(--mute);
  font-size: 11.5px;
  letter-spacing: 0.04em;
}

.legend__item dd {
  margin: 0;
  color: var(--mute);
  line-height: 1.7;
}

.legend__item strong {
  color: var(--bone);
  font-weight: 600;
}

.payload {
  display: inline-block;
  max-width: 300px;
  overflow-wrap: anywhere;
  font-size: 12px;
  color: var(--mute);
}

.file-link {
  display: block;
  font-size: 12px;
}

.actions {
  display: flex;
  gap: 6px;
  white-space: nowrap;
}

.pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 14px;
}

.foot {
  font-size: 13px;
}
</style>
