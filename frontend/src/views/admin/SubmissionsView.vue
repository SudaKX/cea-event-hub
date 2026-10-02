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
  deleteSubmissions,
  listEventSubmissions,
  reviewSubmissions,
} from '@/api/submissions'
import CellText from '@/components/ui/CellText.vue'
import Checkbox from '@/components/ui/Checkbox.vue'
import Modal from '@/components/ui/Modal.vue'
import Pager from '@/components/ui/Pager.vue'
import Select, { type SelectOption } from '@/components/ui/Select.vue'
import SplitPane from '@/components/ui/SplitPane.vue'
import { useDragSelect } from '@/composables/useDragSelect'
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
/**
 * 内容搜索。
 *
 * 输入与"已生效的搜索词"**分开两个变量**：输入框每敲一个字都变，而查询只在按下
 * 回车或点搜索时才发出去。绑成同一个的话，中文输入法还没上屏就会触发查询 ——
 * 拼音字母会当成搜索词发到后端。
 */
const searchInput = ref('')
const appliedSearch = ref('')

const error = ref('')
/**
 * 非错误的提示。现在只有一个来源：批量操作**部分成功** —— 勾选期间有人删掉了
 * 其中几条。那不是失败，但也不能不说，否则管理员会以为全都改了。
 */
const notice = ref('')
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
      q: appliedSearch.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    submissions.value = result.submissions
    total.value = result.total
    selected.value = new Set()
    error.value = ''
    // 提示只关于"上一次批量操作"，重新加载就该消失
    notice.value = ''

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

/*
  待处理队列。

  **只存在内存里，不落库。** 它是一次操作的暂存区：翻页、换筛选都不会丢，刷新或
  关掉页面就清空。落库反而会带来"上次遗留的队列"这种没人能解释来源的状态。

  它存在的理由正是**跨页累积**：列表一次只显示一页，而"把挑出来的十几条一起采用"
  是真实需求 —— 光靠勾选做不到，翻页就把勾选丢了。
*/
const queue = ref<Submission[]>([])

/** 队列里已有的 id，用来去重与判断"是否已入队" */
const queuedIds = computed(() => new Set(queue.value.map((item) => item.id)))

/**
 * 选中项入队。
 *
 * 已在队列里的**不重复添加**：跨页挑选时很容易重复点到同一条，重复入队会让
 * "对队列执行"把同一条处理两次。
 */
function enqueue(): void {
  const additions = submissions.value.filter(
    (item) => selected.value.has(item.id) && !queuedIds.value.has(item.id),
  )
  if (additions.length === 0) return
  queue.value = [...queue.value, ...additions]
  // 入队之后清掉勾选：勾选的意义已经完成，留着只会让人以为它们还"待入队"
  selected.value = new Set()
}

/** 选中项移出队列 */
function dequeue(): void {
  if (selected.value.size === 0) return
  queue.value = queue.value.filter((item) => !selected.value.has(item.id))
  selected.value = new Set()
}

function dropFromQueue(id: number): void {
  queue.value = queue.value.filter((item) => item.id !== id)
}

/** 清空整条队列。攒错了想重来时不必逐条 × */
function clearQueue(): void {
  queue.value = []
}

/**
 * 对**队列里的全部条目**执行操作。
 *
 * 用的是与单条完全相同的接口与错误处理 —— 队列只是一份挑选结果，不该有自己一套
 * 操作路径。批量端点一次请求搞定，不再是逐条 PATCH。做完就清空：那些条目的目的
 * 已经达到，留着会显示过期状态。
 */
async function onQueueReview(status: number): Promise<void> {
  const ids = queue.value.map((item) => item.id)
  if (ids.length === 0) return
  try {
    const reviewed = await reviewSubmissions(ids, status)
    queue.value = []
    await loadSubmissions()
    // 勾选期间可能有人删掉了其中几条，数量对不上时说一声，而不是默默少改几条
    if (reviewed < ids.length) {
      notice.value = `已处理 ${reviewed} 条，另有 ${ids.length - reviewed} 条已不存在`
    }
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '批量改状态失败'
  }
}

async function onQueueDelete(): Promise<void> {
  const ids = queue.value.map((item) => item.id)
  if (ids.length === 0) return
  if (!window.confirm(`删除队列里的 ${ids.length} 条提交？名额会立即释放。`)) return
  try {
    await deleteSubmissions(ids)
    queue.value = []
    await loadSubmissions()
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '批量删除失败'
  }
}

/** 侧栏宽度。也只在内存里 —— 它是这一屏的临时布局，不值得持久化 */
const sideWidth = ref(300)

/*
  选中与按住滑动多选。
  「按下」与「真的滑起来」是两回事，那段状态机在 `useDragSelect` 里，用户页共用
  同一份 —— 抄一份迟早走样。
*/
const {
  set: setSelected,
  onPress,
  onLeave,
  onEnter: onDragEnter,
} = useDragSelect(selected)

/**
 * 行内的复选框、按钮、链接有自己的行为 —— 把它们的点击也当成"选中整行"会让勾选
 * 一次却翻转两次。所以先判断点到了什么。
 *
 * 用 `.checkbox` 这个类而不是 `label` 元素名：复选框的根元素是 div（见 Checkbox
 * 组件里关于 label 会转发点击的说明），靠元素名判断会漏掉。
 */
function onInteractive(target: EventTarget | null): boolean {
  return Boolean(
    (target as HTMLElement | null)?.closest('.checkbox, button, a, input, label'),
  )
}

/**
 * 单击选中／取消选中整行。
 *
 * **双击会先触发两次 click**，所以"点一下选中、点两下看详情"里，双击的那两下会
 * 把选中状态翻转两次 —— 净效果是回到原样，然后打开详情。这是这套手势的固有代价；
 * 想避开只能给单击加延时等待双击，那样每次选择都要卡一下，更糟。
 */
function onRowClick(event: MouseEvent, item: Submission): void {
  if (onInteractive(event.target)) return
  setSelected(item.id, !selected.value.has(item.id))
}

/** 双击打开详情 */
function onRowDoubleClick(event: MouseEvent, item: Submission): void {
  if (onInteractive(event.target)) return
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

// 换活动或换筛选条件都要回到第一页，否则会停在一个新结果集里不存在的页码上。
// 搜索词不在这个列表里 —— 它由 applySearch 显式触发，见那里的说明。
watch([eventId, kind, status], () => {
  page.value = 1
  void loadSubmissions()
})

/**
 * 让搜索词生效。
 *
 * 手动触发而不是 watch 输入框：一是中文输入法未上屏时不该发查询，二是每敲一个字
 * 就查一次的话，"张三"会先按"张"查一遍 —— 那次查询的结果没人要，还占着后端的
 * 一次 LIKE 全表扫描。
 */
function applySearch(): void {
  appliedSearch.value = searchInput.value.trim()
  page.value = 1
  void loadSubmissions()
}

function clearSearch(): void {
  searchInput.value = ''
  appliedSearch.value = ''
  page.value = 1
  void loadSubmissions()
}

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
    <!-- 不是错误，但也得说：否则"改了几条"和"点了几条"对不上时没人知道 -->
    <p v-if="notice" class="ok">{{ notice }}</p>

    <SplitPane v-model="sideWidth" :min="260" :max="560">
      <!--
        筛选在**左栏**、列表上方：它筛的是这一栏里的列表，摆在同一栏里才看得出
        归属；放在分栏之外会显得它同时管着右侧那条跨页累积的队列（它并不管）。
      -->
      <div class="stack">
        <div class="panel filters">
          <div class="filters__row">
            <Select v-model="eventId" label="活动" :options="eventOptions" searchable />

            <Select
              v-model="kind"
              label="分类"
              :options="[{ value: '', label: '全部' }, ...kindOptions]"
              searchable
            />

            <Select v-model="status" label="状态" :options="SUBMISSION_STATUS_OPTIONS" />
          </div>

          <!--
            内容搜索**单独占一行**：它搜的是提交内容那一整段文本，与上面三个"按属性
            筛选"不是一回事，挤在同一行里会让人以为它也只搜某一列。

            这一行不再套"标签在上"的字段结构，与上一行同为横向 flex；搜索框的用途靠
            placeholder 表达，另给 aria-label 保无障碍。

            **必须显式带 `input` 类。** 输入框的外观规则是 `.field input, …, .input`
            —— 出了 `.field` 又没有这个类，它就会回落到浏览器默认外观（白底）。
          -->
          <div class="filters__row">
            <input
              v-model="searchInput"
              class="input filters__search"
              type="search"
              aria-label="在提交内容里搜索"
              placeholder="搜索内容：姓名、备注、任意字段的值……"
              @keyup.enter="applySearch"
            />
            <button class="btn btn--ghost btn--control" type="button" @click="applySearch">
              搜索
            </button>
            <button
              v-if="appliedSearch"
              class="btn btn--ghost btn--control"
              type="button"
              @click="clearSearch"
            >
              清除
            </button>
          </div>
        </div>

        <div class="panel">
          <p v-if="loading" class="empty">加载中…</p>
          <p v-else-if="events.length === 0" class="empty">还没有活动。</p>
          <p v-else-if="submissions.length === 0" class="empty">没有符合条件的提交。</p>
          <!--
          列宽固定：内容是一段长度不受控的 JSON，不钉死列宽的话某一格会撑开整列，
          扫读时眼睛找不到列。看不全的内容由点击整行弹出的详情对话框兜住。
          滚动容器见 .table-scroll —— 它才是"定宽"能成立的前提。
          -->
          <div v-else class="table-scroll">
            <table class="table table--fixed">
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
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="item in submissions"
                  :key="item.id"
                  :class="['row--clickable', { 'row--selected': selected.has(item.id) }]"
                  @click="onRowClick($event, item)"
                  @dblclick="onRowDoubleClick($event, item)"
                >
                  <td>
                    <Checkbox
                      :model-value="selected.has(item.id)"
                      :label="`选择提交 ${item.id}`"
                      @update:model-value="(on) => setSelected(item.id, on)"
                      @press="onPress"
                      @pointerenter="onDragEnter(item.id)"
                      @pointerleave="onLeave"
                    />
                  </td>
                  <td class="num">
                    <!--
                      编号做成按钮，作为**键盘可达**的详情入口：整行单击只对鼠标友好，
                      键盘用户需要一个真正的控件。单击整行则是选中，见 onRowClick。
                    -->
                    <button class="row-link" type="button" @click="detail = item">
                      {{ item.id }}
                    </button>
                  </td>
                  <!--
                    外面必须是 <td>，里面再套一层 flex。
                    直接把 <td> 设成 display:flex 会让它不再是 table-cell，
                    行分隔线与列对齐会跟着断掉。
                  -->
                  <td class="num">
                    <div class="submitter">
                      <!--
                        匿名标识是 `a:<uuid>`，38 个字符，远超这一列宽度，必须截断。
                        标签不能跟着被截 —— 它才是这一列真正要看的信息。
                      -->
                      <CellText class="submitter__id" :text="item.submitter" />
                      <span v-if="!item.from_authenticated_user" class="tag">匿名</span>
                    </div>
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
                </tr>
              </tbody>
            </table>
          </div>

          <!--
            列表底部**分两行**：操作一行、翻页一行。
            挤成一行时三个控件组连成一片，"作用于勾选"和"跳到第几页"混在一起读不通。
            这一行由视图自己渲染而不是塞进 Pager 的插槽 —— 只有这里知道该怎么分行。
          -->
          <div class="pick">
            <span class="num dim pick__count">已选 {{ selected.size }} 行</span>
            <button
              class="btn btn--ghost btn--small"
              type="button"
              :disabled="selected.size === 0"
              title="把选中的行加入队列，之后对队列统一处理。翻页、换筛选都不会丢。"
              @click="enqueue"
            >
              放入队列
            </button>
            <button
              class="btn btn--ghost btn--small"
              type="button"
              :disabled="selected.size === 0"
              title="把选中的行移出队列。"
              @click="dequeue"
            >
              移出队列
            </button>
          </div>

          <Pager
            :page="page"
            :page-size="pageSize"
            :total="total"
            @update:page="onPageChange"
            @update:page-size="onPageSizeChange"
          />
        </div>
      </div>

      <!--
        右侧面板只放**作用于队列**的东西：队列内容 + 对队列执行的操作。
        「放入 / 移出队列」作用于勾选、属于当前这一屏，所以留在列表底部的分页栏里
        —— 摆在哪儿等于声明它管的是哪一片范围。
      -->
      <template #side>
        <div class="panel side">
          <header class="side__head">
            <h2 class="side__title">队列<span class="num dim"> · {{ queue.length }}</span></h2>
            <button
              v-if="queue.length > 0"
              class="btn btn--ghost btn--small"
              type="button"
              @click="clearQueue"
            >
              清空
            </button>
          </header>

          <p v-if="queue.length === 0" class="side__empty dim">
            选中左侧的行后点「放入队列」。<br />
            翻页、换筛选都不会丢，方便攒够一批再一起处理。
          </p>
          <ul v-else class="side__list">
            <li v-for="item in queue" :key="item.id" class="side__item">
              <span class="num side__id">#{{ item.id }}</span>
              <span class="side__who">
                <CellText :text="payloadSummary(item.payload)" />
              </span>
              <span class="tag" :class="`tag--${statusTone(item.status)}`">
                {{ statusLabel(item.status) }}
              </span>
              <button
                class="side__drop"
                type="button"
                :aria-label="`把提交 ${item.id} 移出队列`"
                @click="dropFromQueue(item.id)"
              >
                ×
              </button>
            </li>
          </ul>

          <footer class="side__foot">
            <p class="side__label dim">对队列全部 {{ queue.length }} 条执行</p>
            <div class="side__row">
              <button
                class="btn btn--ghost btn--small"
                type="button"
                :disabled="queue.length === 0"
                title="把队列里的提交标为已采用。只改状态，不删数据、不释放名额。"
                @click="onQueueReview(SUBMISSION_STATUS.ACCEPTED)"
              >
                采用
              </button>
              <button
                class="btn btn--ghost btn--small"
                type="button"
                :disabled="queue.length === 0"
                title="把队列里的提交标为不采用。提交仍在列表里、仍占名额；要腾名额请用「删除」。"
                @click="onQueueReview(SUBMISSION_STATUS.IGNORED)"
              >
                不采用
              </button>
              <button
                class="btn btn--danger btn--small"
                type="button"
                :disabled="queue.length === 0"
                title="删除队列里的提交及其附件，并释放名额。不可撤销。"
                @click="onQueueDelete"
              >
                删除
              </button>
            </div>
          </footer>
        </div>
      </template>
    </SplitPane>

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

/*
  筛选面板是两行横向 flex：三个筛选一行，内容搜索一行。

  用 flex 而不是 `auto-fit` 网格：这里一共就三个筛选，网格的列数由阈值算出来，
  既不好预测、也容易被容器宽度牵着走（右侧面板一拖宽，三个框就一起变窄）。
  `flex: 1 1 200px` 让三者等分且各自不低于 200px，实在放不下才换行。
*/
.filters {
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.filters__row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}

/* 第一行的三个筛选等分；第二行的按钮按内容宽，只有搜索框伸展 */
.filters__row > :not(button) {
  flex: 1 1 200px;
  min-width: 0;
}

/* 搜索框吃掉整行的剩余宽度，按钮不跟着拉长 */
.filters__search {
  flex: 1 1 240px;
  min-width: 0;
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
  这正是"定宽 + 截断"能成立的前提。

  内容列**刻意不给宽度**：它拿剩余空间。所以移除操作列之后，多出来的 210px 会
  自动落到内容列上，不必再调任何数字。
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

/* 固定布局下长串默认会撑破单元格，这里允许它被截断 */
.table--fixed td {
  overflow: hidden;
}

/* 整行可点：单击选中、双击看详情 */
.row--clickable {
  cursor: pointer;
}

.row--clickable:hover {
  background: var(--select-soft);
}

/* 选中的行要有明确底色，否则点完不知道选中了哪些 */
.row--selected,
.row--selected:hover {
  background: var(--select-soft);
}

.row--selected .row-link {
  color: var(--bone);
}

/*
  提交者那一格：标识占满剩余宽度并被截断，标签保持完整。
  `.submitter__id` 落在子组件根元素上 —— Vue 会把父组件的 scope 属性也加到子组件
  根节点，所以这条规则能生效。

  **flex 必须套在这一层 div 上，不能直接给 `<td>`** —— 那会让它不再是 table-cell，
  行分隔线与列对齐会跟着断掉。
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

/*
  列表底部的操作行。与下面的分页行由各自的 border-top 分隔 ——
  两行都贴着一根发丝线，比给它们套一个大边框更像同一块面板里的两层。
*/
.pick {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 12px 14px;
  border-top: 1px solid var(--line);
}

/* 计数与两个按钮是一句话，所以整组靠左排，不用 space-between 把它们拉开 */
.pick__count {
  margin-right: 4px;
  font-size: 12.5px;
}

/* ------------------------------------------------------------------ */
/* 右侧队列面板                                                        */
/* ------------------------------------------------------------------ */

/*
  面板自己撑满侧栏高度：列表长了就自己滚，底部的操作区始终留在视野里 ——
  否则攒了一屏队列之后，"对队列执行"按钮会被顶到看不见的地方。
*/
.side {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.side__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--line);
}

.side__title {
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--mute);
}

.side__empty {
  margin: 0;
  padding: 16px 14px;
  font-size: 12.5px;
  line-height: 1.9;
}

.side__list {
  margin: 0;
  padding: 0;
  list-style: none;
  /* 只有这一段滚，头尾固定 */
  overflow-y: auto;
  flex: 1;
  min-height: 0;
}

.side__item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--line);
  font-size: 12.5px;
}

.side__id {
  flex: none;
  color: var(--dim);
}

/* 摘要占满中间的剩余宽度并截断 —— 队列要的是"认得出是哪条"，不是看全内容 */
.side__who {
  flex: 1;
  min-width: 0;
}

.side__drop {
  flex: none;
  width: 20px;
  height: 20px;
  padding: 0;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--dim);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  transition: color var(--transition-fast);
}

.side__drop:hover {
  color: var(--red-hi);
}

.side__foot {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 14px;
  border-top: 1px solid var(--line);
}

.side__label {
  margin: 0;
  font-size: 12px;
}

.side__row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.foot {
  font-size: 13px;
}
</style>

