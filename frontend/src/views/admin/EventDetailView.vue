<script setup lang="ts">
/** 活动详情：编辑策略、投放内容、查看该活动的提交。 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { deleteEvent, deployContent, getAdminEvent, listContent, updateEvent } from '@/api/events'
import { deleteSubmission, listEventSubmissions, reviewSubmission } from '@/api/submissions'
import { attachmentUrl } from '@/api/submissions'
import Select, { type SelectOption } from '@/components/ui/Select.vue'
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
const error = ref('')
const notice = ref('')
const loading = ref(true)
const busy = ref(false)
const archive = ref<File | null>(null)

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

async function load(): Promise<void> {
  loading.value = true
  try {
    const [detail, content, list] = await Promise.all([
      getAdminEvent(props.eventId),
      listContent(props.eventId),
      listEventSubmissions(props.eventId, { page_size: 20 }),
    ])
    event.value = detail
    files.value = content.files
    submissions.value = list.submissions
    total.value = list.total
    form.value = {
      title: detail.title,
      summary: detail.summary ?? '',
      status: detail.status,
      submission_requires_login: detail.submission_requires_login,
      max_submissions: detail.max_submissions === null ? '' : String(detail.max_submissions),
    }
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

async function onReview(submission: Submission, status: string): Promise<void> {
  try {
    await reviewSubmission(submission.id, status)
    await load()
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '操作失败'
  }
}

async function onDeleteSubmission(submission: Submission): Promise<void> {
  if (!window.confirm('删除这条提交？它占用的名额会立即释放。')) return
  try {
    await deleteSubmission(submission.id)
    await load()
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '删除失败'
  }
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
        <h2 class="block__title">提交<span class="dim"> · 共 {{ total }} 条</span></h2>

        <p v-if="submissions.length === 0" class="empty">还没有提交。</p>
        <table v-else class="table">
          <thead>
            <tr>
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
              <td class="num">{{ item.id }}</td>
              <td class="num">
                {{ item.submitter }}
                <span v-if="!item.from_authenticated_user" class="tag">匿名</span>
              </td>
              <td class="num">{{ item.kind }}</td>
              <td>
                <!-- 按原始键值展示，不假设字段语义 -->
                <code class="payload">{{ JSON.stringify(item.payload) }}</code>
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
                  @click="onDeleteSubmission(item)"
                >
                  删除
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
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

.payload {
  display: inline-block;
  max-width: 320px;
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
</style>
