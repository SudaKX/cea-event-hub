<script setup lang="ts">
/**
 * 邀请码管理（管理台第四个 Tab，仅管理员可见）。
 *
 * 三件事：看全部码、建码、以及两个**应急开关**。普通用户那一侧的入口在个人中心 ——
 * 他们只能碰自己名下的码，这里管的是全部。
 */
import { computed, onMounted, ref } from 'vue'

import { ApiError } from '@/api/client'
import {
  createInvitation,
  listAllInvitations,
  readSwitches,
  revokeInvitation,
  writeSwitch,
} from '@/api/invitations'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import Select from '@/components/ui/Select.vue'
import Switch from '@/components/ui/Switch.vue'
import NumberInput from '@/components/ui/NumberInput.vue'
import type { InvitationCode, PlatformSwitches } from '@/types/api'

const toast = useToast()
const confirm = useConfirm()

const codes = ref<InvitationCode[]>([])
const switches = ref<PlatformSwitches>({
  invitations_paused: false,
  invitation_issuance_paused: false,
})
const loading = ref(true)
const error = ref('')
const busy = ref(false)
const fieldErrors = ref<Record<string, string>>({})

const draft = ref({ token: '', name: '', days: 7, max_uses: 1 })

/* ------------------------------------------------------------------ */
/* 筛选                                                                */
/* ------------------------------------------------------------------ */

/** 空串表示"不限"，与其它管理页的筛选项同一约定 */
const sourceFilter = ref('')
const statusFilter = ref('')

const SOURCE_OPTIONS = [
  { value: '', label: '全部来源' },
  { value: 'platform', label: '平台码（管理员建）' },
  { value: 'user', label: '用户码（个人申请）' },
]

const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'usable', label: '可用' },
  { value: 'expired', label: '已过期' },
  { value: 'exhausted', label: '已用尽' },
  { value: 'revoked', label: '已失效' },
]

/** 失效的与过期的不一样：前者是管理员停用，后者是时间到了 */
function state(code: InvitationCode): { label: string; tone: string; key: string } {
  if (code.revoked_at) return { label: '已失效', tone: 'tag--ignored', key: 'revoked' }
  if (new Date(code.expires_at) <= new Date()) {
    return { label: '已过期', tone: 'tag--off', key: 'expired' }
  }
  if (code.used_count >= code.max_uses) {
    return { label: '已用尽', tone: 'tag--off', key: 'exhausted' }
  }
  return { label: '可用', tone: 'tag--live', key: 'usable' }
}

const filtered = computed(() =>
  codes.value.filter((code) => {
    if (sourceFilter.value === 'platform' && !code.is_platform) return false
    if (sourceFilter.value === 'user' && code.is_platform) return false
    if (statusFilter.value && state(code).key !== statusFilter.value) return false
    return true
  }),
)

const filtering = computed(
  () => sourceFilter.value !== '' || statusFilter.value !== '',
)

const usable = computed(
  () => codes.value.filter((code) => state(code).label === '可用').length,
)

async function reload(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const [list, flags] = await Promise.all([listAllInvitations(), readSwitches()])
    codes.value = list
    switches.value = flags
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(reload)

async function onCreate(): Promise<void> {
  busy.value = true
  fieldErrors.value = {}
  try {
    await createInvitation({
      // 留空即由服务端生成一个可手输的短码
      token: draft.value.token.trim() || undefined,
      name: draft.value.name,
      days: draft.value.days,
      max_uses: draft.value.max_uses,
    })
    toast.ok('邀请码已创建')
    draft.value = { token: '', name: '', days: 7, max_uses: 1 }
    await reload()
  } catch (caught) {
    if (caught instanceof ApiError) {
      fieldErrors.value = caught.fields ?? {}
      toast.fail(caught.message)
    } else {
      toast.fail('创建失败')
    }
  } finally {
    busy.value = false
  }
}

async function onRevoke(code: InvitationCode): Promise<void> {
  const ok = await confirm.ask({
    title: '使邀请码失效',
    message:
      `让 ${code.token}（${code.name}）失效？` +
      '它不能再用于注册，但**已经用过的记录会保留** —— 谁邀请了谁这件事不能因为停用而消失。',
    confirmText: '失效',
    danger: true,
  })
  if (!ok) return

  try {
    await revokeInvitation(code.id)
    toast.ok('已失效')
  } catch (caught) {
    toast.fail(caught instanceof ApiError ? caught.message : '操作失败')
  }
  await reload()
}

/**
 * 改开关。
 *
 * **不需要重启**：它存在库里，下一次请求就读到新值。这正是它不能做成配置项的理由
 * —— 应急刹车等一次重启就失去意义。
 */
async function onToggle(key: keyof PlatformSwitches): Promise<void> {
  const next = !switches.value[key]
  if (next && key === 'invitations_paused') {
    const ok = await confirm.ask({
      title: '暂停邀请',
      message:
        '暂停之后**所有**邀请码都不能用于注册，包括此前已经发出的、仍在有效期内的那些。' +
        '这是应急刹车，用来在码被倒卖时立刻止住。',
      confirmText: '暂停邀请',
      danger: true,
    })
    if (!ok) return
  }

  try {
    switches.value = await writeSwitch(key, next)
    toast.ok(next ? '已暂停' : '已恢复')
  } catch (caught) {
    toast.fail(caught instanceof ApiError ? caught.message : '操作失败')
  }
}
</script>

<template>
  <section class="invites">
    <header class="invites__head">
      <h1 class="invites__title">邀请码</h1>
      <p class="dim invites__lead">
        注册需要持有效邀请码。普通用户可以在个人中心申请自己的码，这里管的是全部。
      </p>
    </header>

    <!-- 两个开关放在最上面：它们是应急动作，出事时不该还要往下翻 -->
    <div class="panel invites__card invites__switches">
      <h2 class="invites__label">平台开关</h2>
      <div class="invites__switch">
        <Switch
          :model-value="switches.invitations_paused"
          label="暂停邀请"
          @request="onToggle('invitations_paused')"
        />
        <span>
          <strong>暂停邀请</strong>
          <span class="dim"> —— 拒绝一切邀请码校验，含此前已发出的</span>
        </span>
      </div>
      <div class="invites__switch">
        <Switch
          :model-value="switches.invitation_issuance_paused"
          label="暂停申请"
          @request="onToggle('invitation_issuance_paused')"
        />
        <span>
          <strong>暂停申请</strong>
          <span class="dim"> —— 用户不能再申请新码，已发出的照常可用</span>
        </span>
      </div>
      <p class="dim invites__hint">改完立即生效，正在注册的人马上就会受影响。</p>
    </div>

    <div class="panel invites__card">
      <h2 class="invites__label">创建邀请码</h2>
      <!--
        用 grid 而不是 flex-wrap：**多余的空间要按比例分给各列**，而不是全堆在最后。
        flex 下每个字段按内容宽度排，右边会空出一大片，看起来像表单没写完。
      -->
      <form class="invites__form" @submit.prevent="onCreate">
        <label class="field invites__field--token">
          <span class="field__label">token（留空则自动生成）</span>
          <input v-model="draft.token" maxlength="64" placeholder="自动生成" />
        </label>
        <label class="field invites__field--name">
          <span class="field__label">名称</span>
          <input v-model="draft.name" maxlength="64" required />
          <span v-if="fieldErrors.name" class="field__error">{{ fieldErrors.name }}</span>
        </label>
        <!--
          **不要用 `<label>` 包住 NumberInput。** label 的悬浮会传播给它的被标注控件
          （内部第一个可标注元素，也就是 `−` 按钮），点击也会被转发过去 —— 悬浮时两个
          按钮一起亮、点标题文字会减一。改成 `<div>` + 显式 `for`，见 NumberInput 的说明。
        -->
        <div class="field invites__field--number">
          <label class="field__label" for="invite-days">有效期（天）</label>
          <NumberInput
            id="invite-days"
            v-model="draft.days"
            label="有效期天数"
            :min="1"
            :max="3650"
          />
        </div>
        <div class="field invites__field--number">
          <label class="field__label" for="invite-max-uses">可用次数</label>
          <NumberInput
            id="invite-max-uses"
            v-model="draft.max_uses"
            label="可用次数"
            :min="1"
          />
        </div>
        <div class="invites__submit">
          <button class="btn btn--primary" type="submit" :disabled="busy">
            {{ busy ? '创建中…' : '创建' }}
          </button>
        </div>
      </form>
    </div>

    <div class="panel invites__card">
      <div class="invites__listhead">
        <h2 class="invites__label">全部邀请码</h2>
        <span class="dim">
          共 {{ codes.length }} 张，其中可用 {{ usable }} 张<template v-if="filtering">
            ；当前筛选出 {{ filtered.length }} 张</template
          >
        </span>
      </div>

      <!-- 筛选放在表头这一行下面：它只影响这张表，不该看起来像全局条件 -->
      <div class="invites__filters">
        <Select v-model="sourceFilter" label="来源" :options="SOURCE_OPTIONS" />
        <Select v-model="statusFilter" label="状态" :options="STATUS_OPTIONS" />
      </div>

      <p v-if="loading" class="empty">加载中…</p>
      <p v-else-if="error" class="alert" role="alert">{{ error }}</p>
      <p v-else-if="codes.length === 0" class="empty">还没有邀请码。</p>
      <p v-else-if="filtered.length === 0" class="empty">没有符合筛选条件的邀请码。</p>
      <div v-else class="table-scroll">
        <table class="table">
          <thead>
            <tr>
              <th>token</th>
              <th>名称</th>
              <th>状态</th>
              <th>有效期至</th>
              <th>可用次数</th>
              <th>已用</th>
              <th>来源</th>
              <th />
            </tr>
          </thead>
          <tbody>
            <tr v-for="code in filtered" :key="code.id">
              <td class="mono">{{ code.token }}</td>
              <td>{{ code.name }}</td>
              <td>
                <span class="tag" :class="state(code).tone">{{ state(code).label }}</span>
              </td>
              <td class="num">{{ new Date(code.expires_at).toLocaleString('zh-CN') }}</td>
              <td class="num">{{ code.max_uses }}</td>
              <td class="num">{{ code.used_count }}</td>
              <td>
                <span class="tag">{{ code.is_platform ? '平台' : '用户' }}</span>
              </td>
              <td class="num">
                <button
                  v-if="!code.revoked_at"
                  class="btn btn--danger btn--small"
                  type="button"
                  @click="onRevoke(code)"
                >
                  失效
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>

<style scoped>
.invites {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

/*
  **`.panel` 只提供表面，不含内边距** —— 每个页面自己给（别的管理页也是这么做的）。
  这一页原先漏了，于是内容贴着卡片边框，看起来像"内边距失效"。值取 16px 18px，
  与 SubmissionsView 的侧栏卡一致。
*/
.invites__card {
  padding: 16px 18px;
}

.invites__head {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.invites__title {
  margin: 0;
  font-size: 20px;
}

.invites__lead {
  margin: 0;
  font-size: 13px;
  line-height: 1.8;
}

.invites__label {
  margin: 0;
  font: 600 12px/1 var(--mono);
  letter-spacing: 0.08em;
  color: var(--mute);
}

.invites__switches {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.invites__switch {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}

.invites__filters {
  /*
    每个筛选项给定宽区间，而不是让它按内容宽度缩着 —— `Select` 是 `width: 100%`，
    放进 flex 行里时基准尺寸就是内容宽，于是"全部来源"这种短标签会挤成一条窄框。
  */
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 230px));
  gap: 12px;
  margin-bottom: 14px;
}

/*
  创建表单：**多余空间按比例分给各列**。

  两个文本字段各占 2 份（token 可能很长），两个数字字段各占 1 份，按钮按内容宽。
  **不要给列设最小宽度** —— 那会让总宽超过内容区，于是表单折成两行（第一版就是这样：
  四个字段的最小宽加起来 600px 再加间距，比卡片内容区还宽）。纯 `fr` 会按比例收缩，
  永远是一行。
*/
.invites__form {
  display: grid;
  grid-template-columns: 2fr 2fr 1fr 1fr auto;
  gap: 12px;
  align-items: end;
  margin-top: 12px;
}

/* `min-width: 0` 解开 grid 项默认的"不小于内容宽度"，否则输入框会把列撑开 */
.invites__form > .field {
  min-width: 0;
}

.invites__submit {
  display: flex;
  align-items: flex-end;
}

/* 窄屏：两列，按钮独占一行 */
@media (max-width: 760px) {
  .invites__form {
    grid-template-columns: 1fr 1fr;
  }

  .invites__submit {
    grid-column: 1 / -1;
  }
}

.invites__hint {
  margin: 0;
  font-size: 12px;
}

.field--narrow {
  width: 120px;
}

.invites__listhead {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
</style>
