<script setup lang="ts">
/**
 * 注册页（两阶段的第一步）。
 *
 * 提交之后账号**还不存在** —— 它要等邮件里的链接被打开才创建。因此这里既没有
 * "注册成功，正在跳转"那种整页状态，也不会自动登录：用户接下来要做的事在邮箱里。
 *
 * "已发送"用 **query 参数**表达而不是组件内的一个 ref：刷新页面之后那仍然是真的
 * （邮件确实发过了），而组件状态会丢，用户会看到一个空表单、以为没提交上去。
 */
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const username = ref('')
const password = ref('')
const email = ref('')
/**
 * 邀请码。**必填** —— 自助注册不是无条件开放的。
 *
 * 失败时服务端只回一句"邀请码不可用"，不区分码不存在、过期、用尽还是平台暂停了邀请：
 * 注册接口匿名可达，区分原因等于把它变成邀请码枚举器（见 docs/auth-contract.md）。
 */
const invitationCode = ref('')
const error = ref('')
const busy = ref(false)

/** 是否处于"已发送，去查收邮件"的状态。由 query 决定，见文件头 */
const sent = computed(() => route.query.sent === '1')
/**
 * 这一次是不是**重入** —— 同一对用户名与邮箱已有一条待验证的占位。
 *
 * 二者必须分开说：重入时**并没有新邮件**发出去，说"已发送"会让用户去邮箱里找
 * 一封不存在的新邮件。
 */
const ongoing = computed(() => route.query.ongoing === '1')

const canSubmit = computed(
  () =>
    username.value.trim().length > 0 &&
    password.value.length > 0 &&
    email.value.trim().length > 0 &&
    invitationCode.value.trim().length > 0,
)

async function onSubmit(): Promise<void> {
  error.value = ''
  busy.value = true
  try {
    const result = await auth.signUp({
      username: username.value.trim(),
      password: password.value,
      email: email.value.trim(),
      invitation_code: invitationCode.value.trim(),
    })
    await router.replace({
      query: { sent: '1', ...(result.ongoing ? { ongoing: '1' } : {}) },
    })
    username.value = ''
    password.value = ''
    email.value = ''
    invitationCode.value = ''
  } catch (caught) {
    if (caught instanceof ApiError) {
      /*
        **只显示一条错误。**

        原先这里是两处一起显示：红色卡片写 `caught.message`（"提交内容有误"），
        字段下面写 `caught.fields` 里的具体原因（"邀请码不可用"）—— 于是邀请码不通过
        时屏幕上同时出现两句，而且**更笼统的那句在上面**，读者先看到的是没用的那句。

        现在统一到红色卡片里，并**优先用字段里的具体原因**：它才是能让人据以行动的
        那一句。字段提示不再单独渲染，避免同一件事说两遍。
      */
      const specific = Object.values(caught.fields ?? {})[0]
      error.value = specific ?? caught.message
    } else {
      error.value = '注册失败，请稍后重试'
    }
  } finally {
    busy.value = false
  }
}

/** 回到表单重填。用于"邮箱写错了"这种最需要立刻改过来的情形。 */
async function backToForm(): Promise<void> {
  await router.replace({ query: {} })
}
</script>

<template>
  <main class="auth">
    <div v-if="sent" class="panel auth__card">
      <h1 class="auth__title">CEA<span class="dim">/</span><em>查收邮件</em></h1>

      <p v-if="ongoing" class="mute auth__lead">
        我们已经给这个邮箱发过一封验证邮件了，<strong>没有重复发送</strong>。
        请到收件箱（含垃圾邮件）里找那封标题为「完成注册」的邮件，点击其中的链接。
      </p>
      <p v-else class="ok">
        验证邮件已发送。请到收件箱（含垃圾邮件）里点击链接完成注册。
      </p>

      <p class="mute auth__lead">
        链接 <strong>10 分钟内</strong>有效，且只能使用一次。在链接被打开之前，
        账号尚未创建。
      </p>
      <p class="dim auth__lead">
        链接过期后，用同样的用户名与邮箱重新提交即可。
      </p>

      <button class="btn btn--ghost" type="button" @click="backToForm">
        返回重新填写
      </button>

      <p class="mute auth__foot">已有账号？<RouterLink to="/login">去登录</RouterLink></p>
    </div>

    <form v-else class="panel auth__card" @submit.prevent="onSubmit">
      <h1 class="auth__title">CEA<span class="dim">/</span><em>注册</em></h1>
      <p class="mute auth__lead">需要邮箱验证，验证通过后账号才会创建。</p>

      <label class="field">
        <span class="field__label">用户名</span>
        <input v-model="username" autocomplete="username" required />
      </label>

      <label class="field">
        <span class="field__label">密码</span>
        <input v-model="password" type="password" autocomplete="new-password" required />
        <span class="field__hint dim">至少 8 个字符。长度比复杂度更有效。</span>
      </label>

      <label class="field">
        <span class="field__label">邮箱</span>
        <input v-model="email" type="email" autocomplete="email" required />
        <span class="field__hint dim">验证链接会发到这里。</span>
      </label>

      <label class="field">
        <span class="field__label">邀请码</span>
        <input
          v-model="invitationCode"
          class="mono"
          autocomplete="off"
          maxlength="64"
          required
        />
        <span class="field__hint dim">向社团成员索取，或由管理员发放。</span>
      </label>

      <p v-if="error" class="alert" role="alert">{{ error }}</p>

      <button class="btn btn--primary" type="submit" :disabled="busy || !canSubmit">
        {{ busy ? '提交中…' : '注册' }}
      </button>

      <p class="mute auth__foot">已有账号？<RouterLink to="/login">去登录</RouterLink></p>
    </form>
  </main>
</template>
