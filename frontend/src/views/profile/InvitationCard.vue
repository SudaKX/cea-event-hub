<script setup lang="ts">
/**
 * 个人中心里的邀请码卡片。
 *
 * **普通用户与管理员看到的不是同一件事。**
 *
 * - 普通成员：申请自己的码（有效期 7 天、可用 1 次，两项都由规格写死）、删除尚未
 *   使用的、查看它被谁在何时用过。
 * - 管理员：没有"自己的码"这回事 —— 那份额度属于普通用户，管理员建码是运营动作，
 *   在管理台统一做。所以这里只给一个入口，否则他会以为功能缺失。
 */
import { computed, onMounted, ref } from 'vue'

import { ApiError } from '@/api/client'
import { deleteInvitation, issueInvitation, listMyInvitations } from '@/api/invitations'
import { useAuthStore } from '@/stores/auth'
import { useConfirm } from '@/composables/useConfirm'
import { useToast } from '@/composables/useToast'
import type { InvitationCode } from '@/types/api'

const auth = useAuthStore()
const toast = useToast()
const confirm = useConfirm()

const codes = ref<InvitationCode[]>([])
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const name = ref('')

/** 是否还能申请：手上没有未使用的、且没有过期的可用码 */
const canIssue = computed(
  () => !codes.value.some((code) => code.used_count < code.max_uses && !code.revoked_at),
)

function state(code: InvitationCode): { label: string; tone: string } {
  if (code.revoked_at) return { label: '已失效', tone: 'tag--ignored' }
  if (code.used_count >= code.max_uses) return { label: '已使用', tone: 'tag--off' }
  if (new Date(code.expires_at) <= new Date()) return { label: '已过期', tone: 'tag--off' }
  return { label: '可用', tone: 'tag--live' }
}

async function reload(): Promise<void> {
  if (!auth.isAdmin) {
    loading.value = true
    try {
      codes.value = await listMyInvitations()
    } catch (caught) {
      error.value = caught instanceof ApiError ? caught.message : '加载失败'
    } finally {
      loading.value = false
    }
  }
}

onMounted(reload)

async function onIssue(): Promise<void> {
  busy.value = true
  try {
    await issueInvitation(name.value)
    name.value = ''
    toast.ok('已申请，可以在下面看到它')
    await reload()
  } catch (caught) {
    toast.fail(caught instanceof ApiError ? caught.message : '申请失败')
  } finally {
    busy.value = false
  }
}

async function onDelete(code: InvitationCode): Promise<void> {
  const ok = await confirm.ask({
    title: '删除邀请码',
    message:
      `删除 ${code.token}（${code.name}）？删除之后它就不能再用于注册了。` +
      '注意：**24 小时内仍然不能再申请新的** —— 那条限制看的是你上次申请的时间。',
    confirmText: '删除',
    danger: true,
  })
  if (!ok) return

  try {
    await deleteInvitation(code.id)
    toast.ok('已删除')
  } catch (caught) {
    toast.fail(caught instanceof ApiError ? caught.message : '删除失败')
  }
  await reload()
}
</script>

<template>
  <section class="invite">
    <!--
      **单一根节点，而且根上不能有注释。**

      这里原先写成两个并列的 `<template v-if>` / `<template v-else>`，各自包一个
      `<section>` —— 那让组件成了**多根**（fragment），而 Vue 无法把父组件传下来的
      class 落到多根上：`<InvitationCard class="panel me__card" />` 里的 `panel`
      会被**静默丢弃**。表现是这张卡的内容没有面板底、散落在两列之间 —— 而组件测试
      查的是文字，全绿。截图才看得出来。

      **第二遍才修对。** 第一次只把分支收进一个 `<section>`，却把这段说明留在了
      `<template>` 的根层 —— 而 Vue 3 里**根级注释同样算节点**，于是它仍是多根，
      class 依旧被丢。注释因此必须放在这个 `<section>` **里面**。

      守门的是 `InvitationsView.spec.ts` 的「父组件传下来的 class 落在根节点上」。
    -->
    <!-- 管理员：只给入口 -->
    <template v-if="auth.isAdmin">
      <h2 class="panel__title">邀请码</h2>
      <p class="panel__lead">
        管理员不申请个人邀请码 —— 建码在管理台统一做，可以指定 token、有效期与可用次数。
      </p>
      <RouterLink class="btn btn--ghost" :to="{ name: 'admin-invitations' }">
        去管理台管理邀请码
      </RouterLink>
    </template>

    <!-- 普通用户：申请、删除、查看 -->
    <template v-else>
      <h2 class="panel__title">邀请码</h2>
      <p class="panel__lead">
        你可以申请一张邀请码交给别人，对方凭它注册。有效期 7 天、只能用一次；
        <strong>同时只能有一张未使用的</strong>，且每 24 小时只能申请一张。
      </p>

      <form v-if="canIssue" class="invite__form" @submit.prevent="onIssue">
        <label class="field">
          <span class="field__label">给这张码起个名字</span>
          <input v-model="name" maxlength="64" placeholder="例如：给张三" required />
        </label>
        <button class="btn btn--primary" type="submit" :disabled="busy">
          {{ busy ? '申请中…' : '申请邀请码' }}
        </button>
      </form>
      <p v-else class="panel__lead">
        你已经有一张可用的邀请码了 —— 用掉或删除它之后才能再申请。
      </p>

      <p v-if="loading" class="empty">加载中…</p>
      <p v-else-if="error" class="alert" role="alert">{{ error }}</p>
      <ul v-else-if="codes.length > 0" class="invite__list">
        <li v-for="code in codes" :key="code.id" class="invite__item">
          <div class="invite__row">
            <span class="mono invite__token">{{ code.token }}</span>
            <span class="tag" :class="state(code).tone">{{ state(code).label }}</span>
            <span class="dim invite__name">{{ code.name }}</span>
            <span class="dim num invite__expiry">
              至 {{ new Date(code.expires_at).toLocaleDateString('zh-CN') }}
            </span>
            <button
              v-if="state(code).label === '可用'"
              class="btn btn--ghost btn--small"
              type="button"
              @click="onDelete(code)"
            >
              删除
            </button>
          </div>
          <ul v-if="code.usages.length > 0" class="invite__usages">
            <li v-for="(usage, index) in code.usages" :key="index" class="dim">
              {{ usage.username ?? '（账号已删除）' }} 于
              {{ new Date(usage.used_at).toLocaleString('zh-CN') }} 使用
            </li>
          </ul>
        </li>
      </ul>
    </template>
  </section>
</template>

<style scoped>
.invite {
  display: flex;
  flex-direction: column;
  gap: 10px;
}


.invite__form {
  display: flex;
  align-items: flex-end;
  gap: 10px;
}

.invite__list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.invite__item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-top: 10px;
  border-top: 1px solid var(--line);
}

.invite__row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 13px;
}

.invite__token {
  letter-spacing: 0.08em;
}

.invite__name {
  font-size: 12px;
}

.invite__expiry {
  font-size: 12px;
}

.invite__usages {
  margin: 0;
  padding: 0 0 0 2px;
  list-style: none;
  font-size: 12px;
  line-height: 1.8;
}
</style>
