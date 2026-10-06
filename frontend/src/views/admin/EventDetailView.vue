<script setup lang="ts">
/** 活动详情：编辑策略、投放内容。提交的查看与审核在「提交」页。 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { deleteEvent, deployContent, getAdminEvent, listContent, updateEvent } from '@/api/events'
import FileInput from '@/components/ui/FileInput.vue'
import Select, { type SelectOption } from '@/components/ui/Select.vue'
import NumberInput from '@/components/ui/NumberInput.vue'
import Switch from '@/components/ui/Switch.vue'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import { EVENT_VISIBILITY, EVENT_VISIBILITY_OPTIONS, parseVisibility } from '@/domain/event'
import type { ContentFile, EventAdmin } from '@/types/api'

const toast = useToast()
const confirm = useConfirm()
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
/**
 * 只有**加载失败**留在这里。
 *
 * 操作结果（保存、投放、删除）走通知 —— 参见 `useToast` 里那张表：加载失败内联
 * 是因为通知会消失、留下一片空白，比一直显示错误更糟。
 */
const error = ref('')
const loading = ref(true)
const busy = ref(false)
const archive = ref<File | null>(null)

const form = ref({
  title: '',
  summary: '',
  status: 'draft',
  visibility: String(EVENT_VISIBILITY.PUBLIC),
  submission_requires_login: false,
  /** null = 留空：条数上限取服务端默认，每人最多表示不限 */
  max_submissions: null as number | null,
  max_per_submitter: null as number | null,
})

/**
 * "允许匿名提交"开关的读写口。
 *
 * 界面上写的是**允许匿名**，而字段存的是**需要登录** —— 一对反向。反转只在这两个
 * 函数里发生：模板里直接 `v-model`，看不见 `!`。散到模板上的话，迟早有一处漏掉，
 * 而"匿名开关反了"这种错误从界面上很难看出来（开关动了、保存也成功，语义却相反）。
 */
const allowAnonymous = computed({
  get: () => !form.value.submission_requires_login,
  set: (allowed: boolean) => {
    form.value.submission_requires_login = !allowed
  },
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
    visibility: String(detail.visibility),
    submission_requires_login: detail.submission_requires_login,
    // 接口给的就是 null（留空），直接用 —— 不必再过一手空串
    max_submissions: detail.max_submissions ?? null,
    max_per_submitter: detail.max_per_submitter ?? null,
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
  try {
    event.value = await updateEvent(props.eventId, {
      title: form.value.title,
      summary: form.value.summary,
      status: form.value.status,
      visibility: parseVisibility(form.value.visibility),
      submission_requires_login: form.value.submission_requires_login,
      // null = 留空。**不能写 Number('')** —— 那是 0，而下限是 1，会被后端拒绝
      max_submissions: form.value.max_submissions,
      max_per_submitter: form.value.max_per_submitter,
    })
    toast.ok('已保存')
  } catch (caught) {
    toast.fail(caught instanceof ApiError ? caught.message : '保存失败')
  } finally {
    busy.value = false
  }
}

async function onDeploy(): Promise<void> {
  if (!archive.value) return
  busy.value = true
  try {
    const result = await deployContent(props.eventId, archive.value)
    toast.ok(`已投放 ${result.file_count} 个文件，内容版本 v${result.content_version}`)
    archive.value = null
    await load()
  } catch (caught) {
    toast.fail(caught instanceof ApiError ? caught.message : '投放失败')
  } finally {
    busy.value = false
  }
}

async function onDeleteEvent(): Promise<void> {
  const ok = await confirm.ask({
    title: '删除活动',
    message: `删除活动 ${props.eventId}？提交与附件会一并移除，此操作不可撤销。`,
    confirmText: '删除',
    danger: true,
  })
  if (!ok) return
  try {
    await deleteEvent(props.eventId)
    // 先推通知再跳转：跳转之后本组件会卸载，但通知栈在 App 层，不受影响
    toast.ok(`活动 ${props.eventId} 已删除`)
    await router.push({ name: 'admin-events' })
  } catch (caught) {
    toast.fail(caught instanceof ApiError ? caught.message : '删除失败')
  }
}

onMounted(load)

// 路由参数变化时组件会被复用，不监听就会停在上一个活动的数据上
watch(() => props.eventId, load)
</script>

<template>
  <!--
    `detail` 给整页一个宽度上限：表单页在宽屏上被拉成一条横线是没法读的。
  -->
  <section class="stack detail">
    <header class="head">
      <div>
        <p class="mute head__crumb">
          <RouterLink to="/admin/events">活动</RouterLink>
          <span class="dim"> / </span>
          <span class="mono">{{ eventId }}</span>
        </p>
        <h1 class="head__title">{{ event?.title ?? '加载中…' }}</h1>
      </div>
    </header>

    <p v-if="error" class="alert" role="alert">{{ error }}</p>

    <div v-if="loading" class="panel empty">加载中…</div>

    <template v-else-if="event">
      <!--
        设置：一个表单、一张卡片。

        这几项本来就互相牵制（可见性影响谁找得到、配额影响谁能交、是否要求登录
        又决定了个人限额可不可信），摆在一起比拆开更容易看清全貌。
      -->
      <form class="cards" @submit.prevent="onSave">
        <section class="panel card card--wide">
          <h2 class="card__title">设置</h2>

          <!--
            卡片内部的成对字段用 `auto-fit`：这里要的**恰恰是**让字段填满一行。
            与卡片流用 `auto-fill` 的目标相反 —— 那边是"别把卡片撑宽"，这边是
            "别在窄卡片里留半行空"。同一个属性、两种意图，混用会两边都不对。
          -->
          <div class="card__grid">
            <label class="field">
              <span class="field__label">标题</span>
              <input v-model="form.title" required />
            </label>

            <Select v-model="form.status" label="状态" :options="STATUS_OPTIONS" />

            <Select
              v-model="form.visibility"
              label="可见性"
              :options="EVENT_VISIBILITY_OPTIONS"
            />

            <!-- 用 `<div>` + 显式 for，不要用 `<label>` 包住 NumberInput（见组件的说明） -->
            <div class="field">
              <label class="field__label" for="detail-max-submissions">
                条数上限<span class="dim">（留空取默认）</span>
              </label>
              <NumberInput
                id="detail-max-submissions"
                v-model="form.max_submissions"
                label="条数上限"
                nullable
                :min="0"
                :null-base="4096"
              />
              <span class="field__hint dim">当前：{{ quotaText }}</span>
            </div>

            <div class="field">
              <label class="field__label" for="detail-max-per-submitter">
                每人最多<span class="dim">（留空不限）</span>
              </label>
              <NumberInput
                id="detail-max-per-submitter"
                v-model="form.max_per_submitter"
                label="每人最多"
                nullable
                :min="1"
              />
            </div>

            <!--
              **开关读的是"允许匿名"，字段存的是"需要登录" —— 一对反向。**
              反转只写在一个 computed 里，不散到模板上；否则模板里到处是 `!`，
              改起来必然有一处漏掉，而这类错误的后果是"匿名开关反了"，很难从界面上看出来。
            -->
            <div class="field">
              <span class="field__label">是否允许匿名提交</span>
              <div class="toggle-row">
                <Switch v-model="allowAnonymous" label="允许匿名提交" />
                <span class="toggle-row__text">允许匿名提交</span>
              </div>
            </div>
          </div>

          <label class="field">
            <span class="field__label">简介</span>
            <textarea v-model="form.summary" />
          </label>

          <p class="card__note dim">
            不公开的活动<strong>不出现在任何公开面</strong>，但知道标识的人仍可直接
            用链接打开 —— 也就是"未公开"，不是"不存在"。置顶的另进首页卡片区。
          </p>

          <!--
            这条限制的边界必须写在界面上，否则管理员会以为它是硬限制。
          -->
          <p class="card__note dim">
            「每人最多」对<strong>匿名</strong>活动只能防误操作：匿名提交者的身份由
            客户端自报，换一个浏览器即可绕过。要真正限制，请关掉上面的"允许匿名提交"
            —— 那时提交者是可核实的登录用户。
          </p>

          <div class="card__actions">
            <button class="btn btn--primary" type="submit" :disabled="busy">保存</button>
          </div>
        </section>
      </form>

      <!--
        与设置无关的独立操作。单独一个卡片流：它们的操作各自即时生效，不参与上面
        那次保存，混在同一个表单里会让人以为要一起提交。
      -->
      <div class="cards cards--even">
        <section class="panel card">
          <h2 class="card__title">网页内容</h2>
          <p class="card__note mute">
            上传 zip 整体替换活动内容目录，版本号会递增。校验不通过时目录**完全不被
            触碰**。
          </p>

          <div class="row">
            <FileInput v-model="archive" accept=".zip" label="选择 zip" />
            <button
              class="btn btn--primary"
              type="button"
              :disabled="busy || !archive"
              @click="onDeploy"
            >
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
        </section>

        <!--
          提交：这里只留入口，列表与审核都在「提交」页。
          同一份列表放两处，两边迟早会漂移出不一致（筛选、分页、权限各自一套）。
        -->
        <section class="panel card">
          <h2 class="card__title">提交</h2>
          <p class="card__note mute">当前配额：{{ quotaText }}</p>
          <RouterLink
            class="btn btn--ghost btn--control"
            :to="{ name: 'admin-submissions', query: { event: eventId } }"
          >
            查看该活动的提交
          </RouterLink>
        </section>

        <!--
          删除从页头挪进卡片：它在页头时离标题很远、没有任何说明，是个容易被误点
          的位置。放进卡片之后，"点错了会发生什么"就写在按钮上方。
        -->
        <section class="panel card card--danger">
          <h2 class="card__title">删除活动</h2>
          <p class="card__note dim">
            连同该活动的<strong>全部提交与附件</strong>一并移除，内容目录也会清空。
            此操作<strong>不可撤销</strong>。
          </p>
          <div class="card__actions">
            <button
              class="btn btn--danger"
              type="button"
              :disabled="busy"
              @click="onDeleteEvent"
            >
              删除活动
            </button>
          </div>
        </section>
      </div>
    </template>
  </section>
</template>

<style scoped>
/*
  整页的宽度上限。表单页在宽屏上被拉成一条横线是没法读的，而卡片流本身的上限
  只管得住卡片、管不住页头。
*/
.detail {
  max-width: 1080px;
}

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

/*
  卡片流：**flex 而不是 grid**。

  grid 的 `repeat(auto-*, minmax(…, 1fr))` 给出的是一组**等宽轨道**，卡片无论内容
  多少都占一样宽。而这几张卡片的内容量差得很远：设置卡有七项，"提交"卡只有两行
  —— 等宽会把后者撑出一大截空白。

  flex 允许**逐张给基础宽度**：设置卡基础更宽、伸展比例也更大，于是它自然占据
  大半个行宽，其余的紧凑排在旁边。换行交给 `flex-wrap`，窄屏时自动落成一列。

  `max-width` 仍然要：即使显示器拉到 4K，表单列也不该横跨整个屏幕。
*/
.cards {
  display: flex;
  flex-wrap: wrap;
  /* 各随内容，不把同一行里矮的卡片拉到和最高的一样高 */
  align-items: flex-start;
  gap: 16px;
  max-width: 1080px;
}

/*
  需要**高度统一**的那一条卡片流。

  与默认的 `flex-start` 相反：那里是"各随内容"，这里是"同一行拉到一样高"。
  两种诉求在同一个界面上都成立 —— 设置卡内容多、旁边几张只有几行，拉齐会让它们
  空一大截；而"网页内容 / 提交 / 删除活动"三张排在一起时，参差不齐的底边更显眼。
*/
.cards--even {
  align-items: stretch;
}

.card {
  /*
    基础宽度 300px，可伸可缩。

    `flex-shrink` 不为 0 是必需的（也就是不要写 `flex: 0 0 …`）：窄屏时卡片要能
    缩进容器里，否则整行会溢出。`min-width: 0` 同理 —— 它允许卡片被压到比内容
    更窄，而不是把容器顶开。
  */
  flex: 1 1 300px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 18px 20px 20px;
}

/* 设置卡：内容最多，基础更宽、伸展比例也更大 */
.card--wide {
  flex: 2 1 460px;
}

.card__title {
  font-size: 14px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--mute);
}

/*
  卡片里的说明文字。行距交给卡片的 `gap`，**不用负边距** —— 负边距是配某个特定
  间隙写死的，卡片间距一改就会把两行挤到一起。踩过一次（见 git 记录里的
  `.block__lead`）。
*/
.card__note {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.8;
}

.card__note strong {
  color: var(--bone);
}

/*
  卡片内部成对字段的排布。

  `auto-fit` 在这里是**对**的：卡片只有三百多像素时落成一列，宽一些时两列并排，
  两种情况都不会留下半行空 —— 这正是 `auto-fit` 折叠空轨道的行为。与卡片流那边
  "别把卡片撑宽"的目标相反。
*/
.card__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 14px;
}

/* 卡片自己的操作行：贴底、靠右 */
.card__actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  /*
    `auto` 把这一行推到底部。高度被拉齐的卡片（见 `.cards--even`）里内容只占
    上半截，按钮跟在内容后面会悬在中间；贴底之后几张卡的按钮落在同一条线上 ——
    那才是"高度统一"看起来对的样子。
    卡片高度恰好等于内容时没有富余空间，`auto` 不起作用。
  */
  margin-top: auto;
}

/* 危险操作的卡片：只在左侧加一条警示边，不把整张卡染红 */
.card--danger {
  border-left: 3px solid var(--red);
}

/* 复选框那一行要占满输入框的高度 */
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
