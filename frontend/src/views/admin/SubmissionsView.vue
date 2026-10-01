<script setup lang="ts">
/**
 * 提交审核：先选活动，再按分类/状态筛选。
 *
 * 分类标签是自由字段（不做语义校验），因此筛选项由**实际出现过的值**推导，
 * 而不是预设一份清单 —— 平台并不知道各活动会用什么标签。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import { ApiError } from '@/api/client'
import { listAdminEvents } from '@/api/events'
import {
  attachmentUrl,
  deleteSubmission,
  deleteSubmissions,
  listEventSubmissions,
  reviewSubmission,
} from '@/api/submissions'
import CellText from '@/components/ui/CellText.vue'
import Modal from '@/components/ui/Modal.vue'
import Pager from '@/components/ui/Pager.vue'
import Select, { type SelectOption } from '@/components/ui/Select.vue'
import {
  SUBMISSION_STATUS,
  SUBMISSION_STATUS_OPTIONS,
  parseStatusFilter,
  payloadSummary,
  statusLabel,
  statusTone,
} from '@/domain/submission'
import SubmissionDetailDialog from './SubmissionDetailDialog.vue'
import type { EventAdmin, Submission } from '@/types/api'

const events = ref<EventAdmin[]>([])
const eventId = ref('')
const route = useRoute()
const kind = ref('')
const status = ref('')
const page = ref(1)
const pageSize = ref(20)

/** 正在查看详情的那一条；null 表示对话框关着 */
const detail = ref<Submission | null>(null)
/** 审核动作说明弹窗 */
const helpOpen = ref(false)

const submissions = ref<Submission[]>([])
const total = ref(0)
const selected = ref<Set<number>>(new Set())
const error = ref('')
const loading = ref(false)

/** 活动下拉：标识 + 标题，两者都要，光看标识认不出是哪个活动 */
const eventOptions = computed<SelectOption[]>(() =>
  events.value.map((item) => ({ value: item.id, label: `${item.id} — ${item.title}` })),
)

/** 分类标签是自由字段，筛选项由**实际出现过的值**推导，不预设清单 */
const kindOptions = computed<SelectOption[]>(() =>
  [...new Set(submissions.value.map((s) => s.kind))]
    .sort()
    .map((value) => ({ value, label: value })),
)

const pageCount = computed(() =>
  Math.max(1, Math.ceil(total.value / Math.max(1, pageSize.value))),
)

async function loadEvents(): Promise<void> {
  try {
    events.value = await listAdminEvents()
    const first = events.value[0]
    if (!eventId.value && first) eventId.value = first.id
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '加载活动失败'
  }
}

/**
 * 从查询串里取活动标识，作为**初始选中项**。
 *
 * 活动详情页的「查看该活动的提交」靠它落到正确的活动上 —— 否则会默认选第一个，
 * 而用户刚看的往往不是第一个。只在列表加载出来之后才认它，避免选到一个不存在
 * （或已删除）的活动，那样页面会空着且看不出原因。
 */
function applyEventFromQuery(): void {
  const wanted = route.query.event
  if (typeof wanted !== 'string' || !wanted) return
  if (events.value.some((item) => item.id === wanted)) eventId.value = wanted
}

async function loadSubmissions(): Promise<void> {
  if (!eventId.value) return
  loading.value = true
  try {
    const result = await listEventSubmissions(eventId.value, {
      kind: kind.value || undefined,
      status: parseStatusFilter(status.value),
      page: page.value,
      page_size: pageSize.value,
    })
    submissions.value = result.submissions
    total.value = result.total
    selected.value = new Set()
    error.value = ''

    // 删到当前页空了就退一页 —— 否则会停在一个已经不存在的页码上，看到一片空白
    if (page.value > pageCount.value) {
      page.value = pageCount.value
      await loadSubmissions()
    }
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '加载提交失败'
  } finally {
    loading.value = false
  }
}

async function onReview(submission: Submission, next: number): Promise<void> {
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

/**
 * 点整行看详情。
 *
 * 行内还有复选框与操作按钮，它们有自己的行为 —— 把它们的点击也当成"看详情"会让
 * 勾选或删除的同时弹出一个对话框。所以先判断点到了什么。
 */
function onRowClick(event: MouseEvent, item: Submission): void {
  const target = event.target as HTMLElement | null
  if (target?.closest('button, a, input, label')) return
  detail.value = item
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

// 换活动或换筛选条件都要回到第一页，否则会停在一个新结果集里不存在的页码上
watch([eventId, kind, status], () => {
  page.value = 1
  void loadSubmissions()
})

onMounted(async () => {
  await loadEvents()
  // 查询串要在活动列表就位之后再认，否则没法判断它是否指向一个存在的活动
  applyEventFromQuery()
  await loadSubmissions()
})
</script>

<template>
  <section class="stack">
    <header class="head">
      <div>
        <h1 class="head__title">
          提交
          <!--
            说明收进这个按钮里，而不是铺一张常驻卡片：它是读一次就够的内容，
            常驻只会把真正要看的东西往下挤。
          -->
          <button
            class="help"
            type="button"
            aria-label="审核动作说明"
            @click="helpOpen = true"
          >
            ?
          </button>
        </h1>
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
      三个动作容易被当成同一件事，实际差别很大，尤其"不采用"并不释放名额 ——
      满额活动上如果只标不采用不删除，活动仍然是满的。
    -->
    <Modal class="help-modal" :open="helpOpen" title="审核动作说明" @close="helpOpen = false">
      <dl class="legend">
        <div class="legend__item">
          <dt class="mono">采用 / 不采用</dt>
          <dd>只改审核状态，供你自己归档。<strong>不删除数据，也不释放名额。</strong></dd>
        </div>
        <div class="legend__item">
          <dt class="mono">删除</dt>
          <dd>真正移除该条提交及其附件，并在同一事务里<strong>释放一个名额</strong>。</dd>
        </div>
        <div class="legend__item">
          <dt class="mono">状态</dt>
          <dd>
            <span class="tag tag--received">1 待处理</span> 新提交的初始状态 ·
            <span class="tag tag--accepted">2 已采用</span> 采用 ·
            <span class="tag tag--ignored">0 不采用</span> 不采用（<strong>不释放名额</strong>）
          </dd>
        </div>
      </dl>
    </Modal>

    <p v-if="error" class="alert" role="alert">{{ error }}</p>

    <!--
      选择规则：候选项来自数据、数量不可预期时开搜索（活动、分类都是），
      固定枚举（状态三档）看得完，不必搜。
    -->
    <div class="panel filters">
      <Select v-model="eventId" label="活动" :options="eventOptions" searchable />

      <Select
        v-model="kind"
        label="分类"
        :options="[{ value: '', label: '全部' }, ...kindOptions]"
        searchable
      />

      <Select v-model="status" label="状态" :options="SUBMISSION_STATUS_OPTIONS" />
    </div>

    <div class="panel">
      <p v-if="loading" class="empty">加载中…</p>
      <p v-else-if="events.length === 0" class="empty">还没有活动。</p>
      <p v-else-if="submissions.length === 0" class="empty">没有符合条件的提交。</p>
      <!--
        列宽固定：内容是一段长度不受控的 JSON，不钉死列宽的话某一格会撑开整列，
        扫读时眼睛找不到列。看不全的内容由 CellValue 的展开入口兜住。
      -->
      <table v-else class="table table--fixed">
        <thead>
          <tr>
            <th class="col-check" />
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
            <td>
              <input
                type="checkbox"
                :checked="selected.has(item.id)"
                @change="toggle(item.id)"
              />
            </td>
            <td class="num">
              <!--
                编号做成按钮：整行可点只对鼠标友好，键盘用户需要一个真正的控件
                才能打开详情。它同时是这一行"可点"的可见提示。
              -->
              <button class="row-link" type="button" @click="detail = item">
                {{ item.id }}
              </button>
            </td>
            <td class="num submitter">
              <!--
                匿名标识是 `a:<uuid>`，38 个字符，远超这一列宽度，必须截断。
                标签不能跟着被截 —— 它才是这一列真正要看的信息。
              -->
              <CellText class="submitter__id" :text="item.submitter" />
              <span v-if="!item.from_authenticated_user" class="tag">匿名</span>
            </td>
            <td class="num kind-cell">
              <CellText :text="item.kind" />
            </td>
            <td class="payload-cell">
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
                @click="onDelete(item)"
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

    <p class="mute foot">
      需要按活动查看策略与内容？<RouterLink to="/admin/events">去活动页</RouterLink>
    </p>

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

/*
  标题右侧的 `?`。做得小而不抢眼 —— 它是"需要时查一下"的入口，
  不该和标题争注意力。
*/
.help {
  width: 20px;
  height: 20px;
  margin-left: 6px;
  padding: 0;
  display: inline-grid;
  place-items: center;
  vertical-align: 2px;
  border: 1px solid var(--line-strong);
  border-radius: 50%;
  background: transparent;
  color: var(--mute);
  font: 600 12px/1 var(--mono);
  cursor: pointer;
  transition:
    color var(--transition-fast),
    border-color var(--transition-fast);
}

.help:hover,
.help:focus-visible {
  color: var(--red-hi);
  border-color: var(--red-hi);
}

/* 说明内容在弹窗里，不再是常驻卡片，所以不带外边距与内边距 */
.legend {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
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

/*
  固定列宽。`table-layout: fixed` 让宽度只由这些类决定，不再随内容抖动 ——
  这正是"定宽 + 截断"能成立的前提。内容列拿剩下的空间。
*/
.table--fixed {
  table-layout: fixed;
}

.col-check {
  width: 36px;
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

/*
  提交者那一格：标识占满剩余宽度并被截断，标签保持完整。
  `.submitter__id` 落在子组件根元素上 —— Vue 会把父组件的 scope 属性也加到子组件
  根节点，所以这条规则能生效。
*/
.submitter {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.submitter__id {
  flex: 1;
  min-width: 0;
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

.foot {
  font-size: 13px;
}
</style>
