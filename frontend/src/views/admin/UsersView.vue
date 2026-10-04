<script setup lang="ts">
/**
 * 用户管理：提权、降权、停用、签发重置令牌。
 *
 * 交互与提交页一致：左栏是筛选与列表，右栏是一条**名单**（跨页累积）以及对它的
 * 批量操作。名单只在内存里 —— 它是一次操作的暂存区，刷新即清空，好过留下一条
 * 没人说得清来源的持久名单。
 *
 * 提权与降权都会**吊销该用户的全部会话** —— 否则降级后的用户在旧会话里仍然
 * 持有管理权限，而"停用"会退化成"下次登录才生效"。
 */
import { computed, onMounted, ref, watch } from 'vue'

import { ApiError, http } from '@/api/client'
import Checkbox from '@/components/ui/Checkbox.vue'
import Pager from '@/components/ui/Pager.vue'
import Select, { type SelectOption } from '@/components/ui/Select.vue'
import SplitPane from '@/components/ui/SplitPane.vue'
import { useConfirm } from '@/composables/useConfirm'
import { useDragSelect } from '@/composables/useDragSelect'
import { useToast } from '@/composables/useToast'
import UserDetailDialog from './UserDetailDialog.vue'
import type { ResetToken, UserAdmin } from '@/types/api'

/** 静态筛选项。空串表示"不限"，与后端"缺省不过滤"对齐 */
const ROLE_OPTIONS: SelectOption[] = [
  { value: '', label: '全部' },
  { value: 'user', label: 'user' },
  { value: 'admin', label: 'admin' },
]

const ACTIVE_OPTIONS: SelectOption[] = [
  { value: '', label: '全部' },
  { value: 'true', label: '启用' },
  { value: 'false', label: '停用' },
]

const users = ref<UserAdmin[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const role = ref('')
const isActive = ref('')
/**
 * 用户名搜索。输入与"已生效的词"**分开两个变量**：查询只在回车或点按钮时才发，
 * 否则中文输入法还没上屏就会拿拼音去查。
 */
const usernameInput = ref('')
const appliedUsername = ref('')

const selected = ref<Set<number>>(new Set())
/** 待处理名单。只在内存里，跨页累积 */
const roster = ref<UserAdmin[]>([])
const detail = ref<UserAdmin | null>(null)
const sideWidth = ref(300)

const toast = useToast()
// 删除不可逆，确认框是它唯一的闸门 —— 用应用自有的那个，而不是 window.confirm
const confirm = useConfirm()

/** 只留**加载失败**。批量改动与令牌签发的反馈走通知（见 useToast 的分工表） */
const error = ref('')
const loading = ref(false)
const issued = ref<ResetToken | null>(null)

/** 选中与按住滑动多选，与提交页共用同一份状态机 */
const {
  set: setSelected,
  onPress,
  onLeave,
  onEnter: onDragEnter,
} = useDragSelect(selected)

const pageCount = computed(() =>
  Math.max(1, Math.ceil(total.value / Math.max(1, pageSize.value))),
)

const rosterIds = computed(() => new Set(roster.value.map((item) => item.id)))

async function load(): Promise<void> {
  loading.value = true
  try {
    const { data } = await http.get<{ users: UserAdmin[]; total: number }>('/admin/users', {
      params: {
        role: role.value || undefined,
        is_active: isActive.value === '' ? undefined : isActive.value === 'true',
        username: appliedUsername.value || undefined,
        page: page.value,
        page_size: pageSize.value,
      },
    })
    users.value = data.users
    total.value = data.total
    selected.value = new Set()
    error.value = ''

    // 过滤后结果变少、或停用了某个人之后，当前页可能已经不存在了。
    // 不退页的话会停在一片空白上，而分页器还说这一页存在。
    if (page.value > pageCount.value) {
      page.value = pageCount.value
      await load()
    }
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '加载失败'
  } finally {
    loading.value = false
  }
}

/** 换筛选条件或重新查询都要回到第一页，否则会停在一个新结果集里不存在的页码上 */
function reload(): void {
  appliedUsername.value = usernameInput.value.trim()
  page.value = 1
  void load()
}

/**
 * 翻页与改每页条数走**显式处理函数**而不是 watch。
 *
 * `load` 里有个"当前页越界就退一页"的自我修正，用 watch 的话那次修正会再触发一次
 * watch，同一个动作发两次请求。
 */
function onPageChange(next: number): void {
  page.value = next
  void load()
}

function onPageSizeChange(next: number): void {
  pageSize.value = next
  page.value = 1
  void load()
}

// 换筛选下拉也回到第一页
watch([role, isActive], () => {
  page.value = 1
  void load()
})

/* ------------------------------------------------------------------ */
/* 名单                                                                */
/* ------------------------------------------------------------------ */

/**
 * 选中项加入名单。
 *
 * 已在名单里的**不重复添加**：跨页挑选时很容易重复点到同一个人，重复入单会让
 * "对名单执行"把同一条处理两次。
 */
function addToRoster(): void {
  const additions = users.value.filter(
    (item) => selected.value.has(item.id) && !rosterIds.value.has(item.id),
  )
  if (additions.length === 0) return
  roster.value = [...roster.value, ...additions]
  // 入单之后清掉勾选：勾选的意义已经完成
  selected.value = new Set()
}

function removeFromRoster(): void {
  if (selected.value.size === 0) return
  roster.value = roster.value.filter((item) => !selected.value.has(item.id))
  selected.value = new Set()
}

function dropFromRoster(id: number): void {
  roster.value = roster.value.filter((item) => item.id !== id)
}

function clearRoster(): void {
  roster.value = []
}

/**
 * 对**名单里的全部用户**执行批量改动。
 *
 * 用批量端点一次请求搞定，不再是逐条 PATCH。做完就清空：那些条目的目的已经达到，
 * 留着会显示过期状态。
 */
async function onRosterUpdate(changes: { role?: string; is_active?: boolean }): Promise<void> {
  const ids = roster.value.map((item) => item.id)
  if (ids.length === 0) return
  try {
    const { data } = await http.post<{ updated: number }>('/admin/users:bulk', {
      ids,
      ...changes,
    })
    roster.value = []
    await load()
    // 期间被删掉的账号会被跳过；数量对不上时说一声，而不是默默少改几个
    if (data.updated < ids.length) {
      toast.ok(`已处理 ${data.updated} 位，另有 ${ids.length - data.updated} 位已不存在`)
    } else {
      toast.ok(`已处理 ${data.updated} 位`)
    }
  } catch (caught) {
    toast.fail(caught instanceof ApiError ? caught.message : '批量操作失败')
  }
}

/* ------------------------------------------------------------------ */
/* 单条：重置令牌                                                       */
/* ------------------------------------------------------------------ */

async function issueToken(user: UserAdmin): Promise<void> {
  issued.value = null
  try {
    const { data } = await http.post<ResetToken>(`/admin/users/${user.id}/reset-token`)
    /*
      明文只出现这一次，必须让管理员当场看到，所以**发的是卡片而不是通知** ——
      通知会自动消失，用它承载"只会显示一次"的东西等于把它弄丢。
    */
    issued.value = data
  } catch (caught) {
    toast.fail(caught instanceof ApiError ? caught.message : '签发失败')
  }
}

async function copyToken(): Promise<void> {
  if (!issued.value) return
  try {
    await navigator.clipboard.writeText(issued.value.token)
    toast.ok('令牌已复制')
  } catch {
    toast.fail('复制失败，请手动选中复制')
  }
}

/* ------------------------------------------------------------------ */
/* 单条：删除账号                                                       */
/* ------------------------------------------------------------------ */

/**
 * 删除是不可逆的，因此**必须**先过确认框，且文案要把代价说全：提交会保留、署名
 * 变成编号、不可撤销。只说"确定删除吗"会让人以为连提交一起没了。
 */
async function onDeleteUser(user: UserAdmin): Promise<void> {
  const ok = await confirm.ask({
    title: '删除账号',
    message:
      `删除 ${user.display_name}（${user.username}）？` +
      '账号将被彻底移除、无法恢复，他也不能再登录。' +
      '他提交过的内容会保留，但署名此后只剩编号，不可撤销。',
    confirmText: '删除',
    danger: true,
  })
  if (!ok) return

  try {
    await http.delete(`/admin/users/${user.id}`)
    detail.value = null
    toast.ok(`已删除 ${user.username}`)
  } catch (caught) {
    toast.fail(caught instanceof ApiError ? caught.message : '删除失败')
  }
  // 成功与否都重拉：失败时也让列表回到服务端的真实状态
  await reload()
}

/* ------------------------------------------------------------------ */
/* 行交互                                                              */
/* ------------------------------------------------------------------ */

/** 行内的复选框与按钮有自己的行为，别把它们的点击也算成"选中整行" */
function onInteractive(target: EventTarget | null): boolean {
  return Boolean((target as HTMLElement | null)?.closest('.checkbox, button, a, input, label'))
}

/** 单击选中／取消选中整行 */
function onRowClick(event: MouseEvent, user: UserAdmin): void {
  if (onInteractive(event.target)) return
  setSelected(user.id, !selected.value.has(user.id))
}

/**
 * 双击打开详情。
 *
 * 双击会先触发两次 click，把选中状态翻转两次 —— 净效果是回到原样，然后打开详情。
 * 想避开只能给单击加延时等待双击，那样每次选择都要卡一下，更糟。
 */
function onRowDoubleClick(event: MouseEvent, user: UserAdmin): void {
  if (onInteractive(event.target)) return
  detail.value = user
}

onMounted(load)
</script>

<template>
  <section class="stack">
    <header class="head">
      <div>
        <h1 class="head__title">用户</h1>
        <p class="mute head__lead">
          提权、降权与停用都会立即吊销该用户的全部会话。系统不允许移除最后一个管理员。
        </p>
      </div>
    </header>

    <p v-if="error" class="alert" role="alert">{{ error }}</p>

    <!-- 令牌明文只出现这一次 -->
    <div v-if="issued" class="panel token">
      <h2 class="token__title">密码重置令牌<span class="dim"> · {{ issued.username }}</span></h2>
      <p class="mute token__lead">
        这串令牌**只会显示这一次**，库里只有摘要。请线下转交给本人，并提醒用后即改。
      </p>
      <code class="token__value">{{ issued.token }}</code>
      <div class="row">
        <button class="btn btn--primary btn--small" @click="copyToken">复制</button>
        <button class="btn btn--ghost btn--small" @click="issued = null">我已记下</button>
      </div>
    </div>

    <SplitPane v-model="sideWidth" :min="260" :max="560">
      <!-- 筛选在左栏、列表上方：它筛的就是这一栏里的列表 -->
      <div class="stack">
        <div class="panel filters">
          <div class="filters__row">
            <Select v-model="role" label="角色" :options="ROLE_OPTIONS" />
            <Select v-model="isActive" label="状态" :options="ACTIVE_OPTIONS" />
          </div>

          <div class="filters__row">
            <input
              v-model="usernameInput"
              class="input filters__search"
              type="search"
              aria-label="按用户名搜索"
              placeholder="搜索用户名"
              @keyup.enter="reload"
            />
            <button class="btn btn--ghost btn--control" type="button" @click="reload">
              查询
            </button>
          </div>
        </div>

        <div class="panel">
          <p v-if="loading" class="empty">加载中…</p>
          <p v-else-if="users.length === 0" class="empty">没有符合条件的用户。</p>
          <div v-else class="table-scroll">
            <table class="table">
              <thead>
                <tr>
                  <th class="col-check" />
                  <th class="col-id">#</th>
                  <th>用户名</th>
                  <th>显示名</th>
                  <th>角色</th>
                  <th>状态</th>
                  <th>邮箱</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="user in users"
                  :key="user.id"
                  :class="['row--clickable', { 'row--selected': selected.has(user.id) }]"
                  @click="onRowClick($event, user)"
                  @dblclick="onRowDoubleClick($event, user)"
                >
                  <td>
                    <Checkbox
                      :model-value="selected.has(user.id)"
                      :label="`选择用户 ${user.username}`"
                      @update:model-value="(on) => setSelected(user.id, on)"
                      @press="onPress"
                      @pointerenter="onDragEnter(user.id)"
                      @pointerleave="onLeave"
                    />
                  </td>
                  <td class="num">
                    <!-- 编号做成按钮，作为**键盘可达**的详情入口 -->
                    <button class="row-link" type="button" @click="detail = user">
                      {{ user.id }}
                    </button>
                  </td>
                  <td class="num">{{ user.username }}</td>
                  <td>{{ user.display_name }}</td>
                  <td>
                    <span class="tag" :class="user.role === 'admin' ? 'tag--live' : ''">
                      {{ user.role }}
                    </span>
                  </td>
                  <td>
                    <span class="tag" :class="user.is_active ? '' : 'tag--off'">
                      {{ user.is_active ? '启用' : '停用' }}
                    </span>
                  </td>
                  <td class="num dim">
                    {{ user.email ?? '—' }}
                    <span v-if="user.email && !user.email_verified" class="tag">未验证</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <!--
            列表底部**分两行**：操作一行、翻页一行。
            按钮摆在哪儿等于声明它管的是哪一片范围 —— 「加入 / 移出名单」作用于勾选
            （本页临时），所以留在列表这一侧。
          -->
          <div class="pick">
            <span class="num dim pick__count">已选 {{ selected.size }} 位</span>
            <button
              class="btn btn--ghost btn--small"
              type="button"
              :disabled="selected.size === 0"
              title="把选中的用户加入名单，之后对名单统一处理。翻页、换筛选都不会丢。"
              @click="addToRoster"
            >
              加入名单
            </button>
            <button
              class="btn btn--ghost btn--small"
              type="button"
              :disabled="selected.size === 0"
              title="把选中的用户移出名单。"
              @click="removeFromRoster"
            >
              移出名单
            </button>
          </div>

          <Pager
            :page="page"
            :page-size="pageSize"
            :total="total"
            unit="人"
            @update:page="onPageChange"
            @update:page-size="onPageSizeChange"
          />
        </div>
      </div>

      <!--
        右栏只放**作用于名单**的东西：名单内容 + 对名单执行的操作。
        单条的重置令牌不在这里 —— 它一次只对一个人有意义，双击行即可看到。
      -->
      <template #side>
        <div class="panel side">
          <header class="side__head">
            <h2 class="side__title">名单<span class="num dim"> · {{ roster.length }}</span></h2>
            <button
              v-if="roster.length > 0"
              class="btn btn--ghost btn--small"
              type="button"
              @click="clearRoster"
            >
              清空
            </button>
          </header>

          <p v-if="roster.length === 0" class="side__empty dim">
            选中左侧的行后点「加入名单」。<br />
            翻页、换筛选都不会丢，方便攒够一批再一起处理。
          </p>
          <ul v-else class="side__list">
            <li v-for="user in roster" :key="user.id" class="side__item">
              <span class="num side__id">#{{ user.id }}</span>
              <span class="side__who">{{ user.username }}</span>
              <span class="tag" :class="user.role === 'admin' ? 'tag--live' : ''">
                {{ user.role }}
              </span>
              <button
                class="side__drop"
                type="button"
                :aria-label="`把 ${user.username} 移出名单`"
                @click="dropFromRoster(user.id)"
              >
                ×
              </button>
            </li>
          </ul>

          <footer class="side__foot">
            <p class="side__label dim">对名单全部 {{ roster.length }} 位执行</p>
            <div class="side__row">
              <button
                class="btn btn--ghost btn--small"
                type="button"
                :disabled="roster.length === 0"
                title="把这批人提为管理员。**会立即吊销他们各自的会话**，需要重新登录。"
                @click="onRosterUpdate({ role: 'admin' })"
              >
                提权
              </button>
              <button
                class="btn btn--ghost btn--small"
                type="button"
                :disabled="roster.length === 0"
                title="把这批人降为普通用户。会立即吊销他们各自的会话。系统不允许移除最后一个管理员。"
                @click="onRosterUpdate({ role: 'user' })"
              >
                降权
              </button>
              <button
                class="btn btn--danger btn--small"
                type="button"
                :disabled="roster.length === 0"
                title="停用这批账号，立即吊销会话。不能停用自己的账号。"
                @click="onRosterUpdate({ is_active: false })"
              >
                停用
              </button>
              <button
                class="btn btn--ghost btn--small"
                type="button"
                :disabled="roster.length === 0"
                title="重新启用这批账号。"
                @click="onRosterUpdate({ is_active: true })"
              >
                启用
              </button>
            </div>

            <p class="dim side__hint">
              重置令牌只对单个人有意义，双击列表行查看详情后操作。
            </p>
          </footer>
        </div>
      </template>
    </SplitPane>

    <UserDetailDialog :user="detail" @close="detail = null" @delete="onDeleteUser" />
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

/* ------------------------------------------------------------------ */
/* 筛选面板：两行横向 flex                                             */
/* ------------------------------------------------------------------ */

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

/* 第一行的两个筛选等分；第二行的按钮按内容宽，只有搜索框伸展 */
.filters__row > :not(button) {
  flex: 1 1 200px;
  min-width: 0;
}

.filters__search {
  flex: 1 1 240px;
  min-width: 0;
}

/* ------------------------------------------------------------------ */
/* 列表                                                                */
/* ------------------------------------------------------------------ */

.col-check {
  width: 36px;
}

.col-id {
  width: 64px;
}

.row--clickable {
  cursor: pointer;
}

.row--clickable:hover {
  background: var(--select-soft);
}

.row--selected,
.row--selected:hover {
  background: var(--select-soft);
}

.row--selected .row-link {
  color: var(--bone);
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

/* 列表底部两行：操作一行、翻页一行 */
.pick {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 12px 14px;
  border-top: 1px solid var(--line);
}

.pick__count {
  margin-right: 4px;
  font-size: 12.5px;
}

/* ------------------------------------------------------------------ */
/* 右栏名单面板                                                        */
/* ------------------------------------------------------------------ */

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

.side__who {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--mono);
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

.side__hint {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.7;
}

/* ------------------------------------------------------------------ */
/* 令牌卡片                                                            */
/* ------------------------------------------------------------------ */

.token {
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  border-color: var(--red);
}

.token__title {
  font-size: 14px;
}

.token__lead {
  margin: 0;
  font-size: 12.5px;
}

.token__value {
  padding: 10px 12px;
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: var(--radius-surface);
  font-size: 13px;
  word-break: break-all;
}
</style>
