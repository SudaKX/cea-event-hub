<script setup lang="ts">
/** 活动详情：编辑策略、投放内容。提交的查看与审核在「提交」页。 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { deleteEvent, deployContent, getAdminEvent, listContent, updateEvent } from '@/api/events'
import Checkbox from '@/components/ui/Checkbox.vue'
import FileInput from '@/components/ui/FileInput.vue'
import Select, { type SelectOption } from '@/components/ui/Select.vue'
import type { ContentFile, EventAdmin } from '@/types/api'

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

async function load(): Promise<void> {
  loading.value = true
  try {
    await loadDetail()
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

async function onDeleteEvent(): Promise<void> {
  if (!window.confirm(`删除活动 ${props.eventId}？提交与附件会一并移除，不可撤销。`)) return
  try {
    await deleteEvent(props.eventId)
    await router.push({ name: 'admin-events' })
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '删除失败'
  }
}

onMounted(load)

// 路由参数变化时组件会被复用，不监听就会停在上一个活动的数据上
watch(() => props.eventId, load)
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
          <label class="field">
            <span class="field__label">条数上限<span class="dim">（留空取默认）</span></span>
            <input v-model="form.max_submissions" type="number" min="0" />
            <span class="field__hint dim">当前：{{ quotaText }}</span>
          </label>

          <!--
            复选框自成一行控件：给它一个和输入框等高的行，标签才不会在两列网格里
            被挤着折行。原来它是裸的 <input>，被 .field input 的 width:100% 撑满，
            文字只剩几像素。
          -->
          <div class="field">
            <span class="field__label">提交</span>
            <div class="toggle-row">
              <Checkbox v-model="form.submission_requires_login" label="提交需要登录" />
              <span class="toggle-row__text" @click="form.submission_requires_login = !form.submission_requires_login">
                提交需要登录
              </span>
            </div>
          </div>
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
          <FileInput v-model="archive" accept=".zip" label="选择 zip" />
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

      <!--
        提交：这里只留入口，列表与审核都在「提交」页。
        同一份列表放两处，两边迟早会漂移出不一致（筛选、分页、权限各自一套）。
      -->
      <div class="panel block">
        <div class="block__row">
          <div class="block__head">
            <h2 class="block__title">提交</h2>
            <p class="mute block__lead">当前配额：{{ quotaText }}</p>
          </div>
          <RouterLink
            class="btn btn--ghost btn--control"
            :to="{ name: 'admin-submissions', query: { event: eventId } }"
          >
            查看该活动的提交
          </RouterLink>
        </div>
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

/* 标题 + 说明在左、操作在右。按钮与输入框同高，视觉上才压得住这一行 */
.block__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

/*
  标题与说明之间的行距交给 gap，不用负边距。
  `.block__lead` 自带的 -6px 是配 `.block` 的 14px 间隙用的（净剩 8px）；搬进这里
  之后父级不再是那个 flex 容器，负边距直接把两行挤到一起。
*/
.block__head {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.block__head .block__lead {
  margin: 0;
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
</style>
