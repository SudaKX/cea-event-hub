<script setup lang="ts">
/** 活动详情：编辑策略、投放内容、查看该活动的提交。 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { deleteEvent, deployContent, getAdminEvent, listContent, updateEvent } from '@/api/events'
import { deleteSubmission, listEventSubmissions, reviewSubmission } from '@/api/submissions'
import { attachmentUrl } from '@/api/submissions'
import CellText from '@/components/ui/CellText.vue'
import Pager from '@/components/ui/Pager.vue'
import Select, { type SelectOption } from '@/components/ui/Select.vue'
import {
  SUBMISSION_STATUS,
  payloadSummary,
  statusLabel,
  statusTone,
} from '@/domain/submission'
import SubmissionDetailDialog from './SubmissionDetailDialog.vue'
import type { ContentFile, EventAdmin, Submission } from '@/types/api'

const props = defineProps<{ eventId: string }>()

/** 状态选项把后果写在标签里 —— 光看 draft/live/archived 不知道意味着什么 */
const STATUS_OPTIONS: SelectOption[] = [
  { value: 'draft', label: 'draft（不出现在公开页面）' },
  { value: 'live', label: 'live（公开可见）' },
  { value: 'archived', label: 'archived（已归档）' },
]

const router = useRouter()
const event = ref<EventAdmin | null>(null)
const files = ref<ContentFile[]>([])
const submissions = ref<Submission[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
/** 正在查看详情的那一条；null 表示对话框关着 */
const detail = ref<Submission | null>(null)
const error = ref('')
const notice = ref('')
const loading = ref(true)
const busy = ref(false)
const archive = ref<File | null>(null)

const pageCount = computed(() =>
  Math.max(1, Math.ceil(total.value / Math.max(1, pageSize.value))),
)

const form = ref({
  title: '',
  summary: '',
  status: 'draft',
  submission_requires_login: false,
  max_submissions: '' as string,
})

const quotaText = computed(() => {
  const quota = event.value?.quota
  if (!quota) return '—'
  if (quota.limit === null) return `不限（已收 ${quota.used}）`
  return `${quota.used} / ${quota.limit}`
})

/**
 * 拉活动详情与内容，并**用返回值重置表单**。
 *
 * 只在"进页面 / 换活动 / 投放内容"时调用 —— 表单里可能有管理员正在敲的内容，
 * 不能因为翻个页或者删掉一条垃圾提交就把它冲掉。
 */
async function loadDetail(): Promise<void> {
  const [detail, content] = await Promise.all([
    getAdminEvent(props.eventId),
    listContent(props.eventId),
  ])
  event.value = detail
  files.value = content.files
  form.value = {
    title: detail.title,
    summary: detail.summary ?? '',
    status: detail.status,
    submission_requires_login: detail.submission_requires_login,
    max_submissions: detail.max_submissions === null ? '' : String(detail.max_submissions),
  }
}

/**
 * 只拉一页提交。
 *
 * 翻页、审核都走这里，不碰表单也不重取内容。
 */
async function loadSubmissions(): Promise<void> {
  const list = await listEventSubmissions(props.eventId, {
    page: page.value,
    page_size: pageSize.value,
  })
  submissions.value = list.submissions
  total.value = list.total

  // 删到当前页空了就退一页 —— 否则会停在一个已经不存在的页码上，看到一片空白
  if (page.value > pageCount.value) {
    page.value = pageCount.value
    await loadSubmissions()
  }
}

/** 删除会改变配额，但**不该重置表单** —— 所以只重取活动本身 */
async function refreshQuota(): Promise<void> {
  event.value = await getAdminEvent(props.eventId)
}

/**
 * 翻页与改每页条数走**显式处理函数**而不是 watch。
 *
 * 因为 `loadSubmissions` 里有个"当前页越界就退一页"的自我修正，用 watch 的话那次
 * 修正会再触发一次 watch，同一个动作发两次请求。
 */
function onPageChange(next: number): void {
  page.value = next
  void loadSubmissions()
}

function onPageSizeChange(next: number): void {
  pageSize.value = next
  // 每页条数变了必须回到第一页，否则会停在一个可能已不存在的页码上
  page.value = 1
  void loadSubmissions()
}

async function load(): Promise<void> {
  loading.value = true
  try {
    await loadDetail()
    await loadSubmissions()
    error.value = ''
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function onSave(): Promise<void> {
  busy.value = true
  notice.value = ''
  try {
    event.value = await updateEvent(props.eventId, {
      title: form.value.title,
      summary: form.value.summary,
      status: form.value.status,
      submission_requires_login: form.value.submission_requires_login,
      max_submissions: form.value.max_submissions === '' ? null : Number(form.value.max_submissions),
    })
    notice.value = '已保存'
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '保存失败'
  } finally {
    busy.value = false
  }
}

async function onDeploy(): Promise<void> {
  if (!archive.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await deployContent(props.eventId, archive.value)
    notice.value = `已投放 ${result.file_count} 个文件，内容版本 v${result.content_version}`
    archive.value = null
    await load()
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '投放失败'
  } finally {
    busy.value = false
  }
}

async function onReview(submission: Submission, status: number): Promise<void> {
  try {
    await reviewSubmission(submission.id, status)
    // 只改了状态，重取这一页就够了
    await loadSubmissions()
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '操作失败'
  }
}

async function onDeleteSubmission(submission: Submission): Promise<void> {
  if (!window.confirm('删除这条提交？它占用的名额会立即释放。')) return
  try {
    await deleteSubmission(submission.id)
    // 配额变了要刷新活动，但表单不动 —— 管理员可能正在改它
    await Promise.all([loadSubmissions(), refreshQuota()])
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '删除失败'
  }
}

/**
 * 点整行看详情。
 *
 * 行内还有操作按钮，它们有自己的行为 —— 把它们的点击也当成"看详情"会让人在删除
 * 的同时弹出一个对话框。所以先判断点到了什么。
 */
function onRowClick(event: MouseEvent, item: Submission): void {
  const target = event.target as HTMLElement | null
  if (target?.closest('button, a, input, label')) return
  detail.value = item
}

async function onDeleteEvent(): Promise<void> {
  if (!window.confirm(`删除活动 ${props.eventId}？提交与附件会一并移除，不可撤销。`)) return
  try {
    await deleteEvent(props.eventId)
    await router.push({ name: 'admin-events' })
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '删除失败'
  }
}

function onFileChange(input: Event): void {
  const target = input.target as HTMLInputElement
  archive.value = target.files?.[0] ?? null
}

onMounted(load)

// 路由参数变化时组件会被复用，不监听就会停在上一个活动的数据上（顺带把页码归位）
watch(
  () => props.eventId,
  async () => {
    page.value = 1
    await load()
  },
)
</script>

<template>
  <section class="stack">
    <header class="head">
      <div>
        <p class="mute head__crumb">
          <RouterLink to="/admin/events">活动</RouterLink>
          <span class="dim"> / </span>
          <span class="mono">{{ eventId }}</span>
        </p>
        <h1 class="head__title">{{ event?.title ?? '加载中…' }}</h1>
      </div>
      <button class="btn btn--danger btn--small" type="button" @click="onDeleteEvent">
        删除活动
      </button>
    </header>

    <p v-if="error" class="alert" role="alert">{{ error }}</p>
    <p v-if="notice" class="ok">{{ notice }}</p>

    <div v-if="loading" class="panel empty">加载中…</div>

    <template v-else-if="event">
      <!-- 策略 -->
      <form class="panel block" @submit.prevent="onSave">
        <h2 class="block__title">提交策略</h2>

        <div class="grid">
          <label class="field">
            <span class="field__label">标题</span>
            <input v-model="form.title" required />
          </label>

          <Select v-model="form.status" label="状态" :options="STATUS_OPTIONS" />
        </div>

        <label class="field">
          <span class="field__label">简介</span>
          <textarea v-model="form.summary" />
        </label>

        <div class="grid">
          <label class="field field--inline">
            <input v-model="form.submission_requires_login" type="checkbox" />
            <span>提交需要登录</span>
          </label>

          <label class="field">
            <span class="field__label">条数上限<span class="dim">（留空取默认）</span></span>
            <input v-model="form.max_submissions" type="number" min="0" />
            <span class="field__hint dim">当前：{{ quotaText }}</span>
          </label>
        </div>

        <button class="btn btn--primary" type="submit" :disabled="busy">保存</button>
      </form>

      <!-- 内容投放 -->
      <div class="panel block">
        <h2 class="block__title">网页内容</h2>
        <p class="mute block__lead">
          上传 zip 整体替换活动内容目录，版本号会递增。校验不通过时目录**完全不被触碰**。
        </p>

        <div class="row">
          <input type="file" accept=".zip" @change="onFileChange" />
          <button class="btn btn--primary" type="button" :disabled="busy || !archive" @click="onDeploy">
            {{ busy ? '投放中…' : '投放' }}
          </button>
        </div>

        <p v-if="files.length === 0" class="empty">还没有投放内容。</p>
        <ul v-else class="files">
          <li v-for="file in files" :key="file.path" class="files__item">
            <span class="mono grow">{{ file.path }}</span>
            <span class="num dim">{{ file.size_bytes }} B</span>
          </li>
        </ul>
      </div>

      <!-- 提交 -->
      <div class="panel block">
        <!--
          总数交给 Pager 显示，这里不重复一遍 —— 两个地方各显示一份数字，
          迟早会出现对不上的时候。
        -->
        <h2 class="block__title">提交</h2>

        <p v-if="submissions.length === 0" class="empty">还没有提交。</p>
        <!-- 列宽固定，理由同提交页：内容长度不受控，不钉死列宽会撑开整列 -->
        <table v-else class="table table--fixed">
          <thead>
            <tr>
              <th class="col-id">#</th>
              <th class="col-submitter">提交者</th>
              <th class="col-kind">分类</th>
              <th class="col-payload">内容</th>
              <th class="col-files">附件</th>
              <th class="col-status">状态</th>
              <th class="col-time">时间</th>
              <th class="col-actions">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in submissions"
              :key="item.id"
              class="row--clickable"
              @click="onRowClick($event, item)"
            >
              <td class="num">
                <!-- 整行可点只对鼠标友好，键盘用户需要这个真正的控件 -->
                <button class="row-link" type="button" @click="detail = item">
                  {{ item.id }}
                </button>
              </td>
              <td class="num">
                {{ item.submitter }}
                <span v-if="!item.from_authenticated_user" class="tag">匿名</span>
              </td>
              <td class="num">{{ item.kind }}</td>
              <td>
                <!-- 按原始键值展示，不假设字段语义；$display 只影响摘要那一行 -->
                <CellText :text="payloadSummary(item.payload)" />
              </td>
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
              <td>
                <span class="tag" :class="`tag--${statusTone(item.status)}`">
                  {{ statusLabel(item.status) }}
                </span>
              </td>
              <td class="num dim">{{ new Date(item.created_at).toLocaleString('zh-CN') }}</td>
              <td class="actions">
                <button
                  class="btn btn--ghost btn--small"
                  title="标记为已采用。只改状态，不删数据、不释放名额。"
                  @click="onReview(item, SUBMISSION_STATUS.ACCEPTED)"
                >
                  采用
                </button>
                <button
                  class="btn btn--ghost btn--small"
                  title="标记为不采用。提交仍会留在列表里，仍占用名额；要腾出名额请用「删除」。"
                  @click="onReview(item, SUBMISSION_STATUS.IGNORED)"
                >
                  不采用
                </button>
                <button
                  class="btn btn--danger btn--small"
                  title="删除该条提交及其附件，并释放一个名额。不可撤销。"
                  @click="onDeleteSubmission(item)"
                >
                  删除
                </button>
              </td>
            </tr>
          </tbody>
        </table>

        <Pager
          :page="page"
          :page-size="pageSize"
          :total="total"
          @update:page="onPageChange"
          @update:page-size="onPageSizeChange"
        />
      </div>
    </template>

    <SubmissionDetailDialog :submission="detail" @close="detail = null" />
  </section>
</template>

<style scoped>
.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.head__crumb {
  margin: 0 0 6px;
  font-size: 12px;
}

.head__title {
  font-size: 20px;
}

.block {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.block__title {
  font-size: 14px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--mute);
}

.block__lead {
  margin: -6px 0 0;
  font-size: 13px;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 14px;
}

.files {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
}

.files__item {
  display: flex;
  gap: 12px;
  padding: 4px 0;
  border-bottom: 1px solid var(--line);
}

/*
  固定列宽。`table-layout: fixed` 让宽度只由这些类决定，不再随内容抖动 ——
  这正是"定宽 + 截断"能成立的前提。内容列拿剩下的空间。
*/
.table--fixed {
  table-layout: fixed;
}

.col-id {
  width: 64px;
}

.col-submitter {
  width: 168px;
}

.col-kind {
  width: 96px;
}

.col-files {
  width: 140px;
}

.col-status {
  width: 88px;
}

.col-time {
  width: 168px;
}

.col-actions {
  width: 210px;
}

/* 固定布局下长串默认会撑破单元格，这里允许它被截断 */
.table--fixed td {
  overflow: hidden;
}

/* 整行可点：给鼠标用户一个更大的目标，也给"这行有详情"一个视觉暗示 */
.row--clickable {
  cursor: pointer;
}

.row--clickable:hover {
  background: rgba(255, 255, 255, 0.03);
}

/* 编号做成按钮，作为键盘可达的入口。去掉按钮的外观，只留可点与焦点态 */
.row-link {
  padding: 0;
  border: 0;
  background: none;
  color: var(--red-hi);
  font: inherit;
  cursor: pointer;
}

.row-link:hover {
  text-decoration: underline;
}

.file-link {
  display: block;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.actions {
  display: flex;
  gap: 6px;
  white-space: nowrap;
}
</style>
