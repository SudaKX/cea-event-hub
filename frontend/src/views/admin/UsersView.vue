<script setup lang="ts">
/**
 * 用户管理：提权、降权、停用、签发重置令牌。
 *
 * 提权与降级都会**吊销该用户的全部会话** —— 否则降级后的用户在旧会话里仍然
 * 持有管理权限，而"停用"会退化成"下次登录才生效"。
 */
import { onMounted, ref } from 'vue'

import { ApiError, http } from '@/api/client'
import type { ResetToken, UserAdmin } from '@/types/api'

const users = ref<UserAdmin[]>([])
const total = ref(0)
const role = ref('')
const isActive = ref('')
const username = ref('')
const error = ref('')
const notice = ref('')
const loading = ref(false)
const issued = ref<ResetToken | null>(null)

async function load(): Promise<void> {
  loading.value = true
  try {
    const { data } = await http.get<{ users: UserAdmin[]; total: number }>('/admin/users', {
      params: {
        role: role.value || undefined,
        is_active: isActive.value === '' ? undefined : isActive.value === 'true',
        username: username.value || undefined,
      },
    })
    users.value = data.users
    total.value = data.total
    error.value = ''
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function patch(user: UserAdmin, changes: Record<string, unknown>): Promise<void> {
  notice.value = ''
  try {
    await http.patch(`/admin/users/${user.id}`, changes)
    await load()
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '操作失败'
  }
}

async function issueToken(user: UserAdmin): Promise<void> {
  notice.value = ''
  issued.value = null
  try {
    const { data } = await http.post<ResetToken>(`/admin/users/${user.id}/reset-token`)
    // 明文只出现这一次，因此必须让管理员当场看到
    issued.value = data
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '签发失败'
  }
}

async function copyToken(): Promise<void> {
  if (!issued.value) return
  try {
    await navigator.clipboard.writeText(issued.value.token)
    notice.value = '令牌已复制'
  } catch {
    notice.value = '复制失败，请手动选中复制'
  }
}

onMounted(load)
</script>

<template>
  <section class="stack">
    <header class="head">
      <div>
        <h1 class="head__title">用户</h1>
        <p class="mute head__lead">
          提权与停用会立即吊销该用户的全部会话。系统不允许移除最后一个管理员。
        </p>
      </div>
      <span class="num dim">共 {{ total }} 人</span>
    </header>

    <p v-if="error" class="alert" role="alert">{{ error }}</p>
    <p v-if="notice" class="ok">{{ notice }}</p>

    <!-- 令牌明文只出现这一次 -->
    <div v-if="issued" class="panel token">
      <h2 class="token__title">口令重置令牌<span class="dim"> · {{ issued.username }}</span></h2>
      <p class="mute token__lead">
        这串令牌**只会显示这一次**，库里只有摘要。请线下转交给本人，并提醒用后即改。
      </p>
      <code class="token__value">{{ issued.token }}</code>
      <div class="row">
        <button class="btn btn--primary btn--small" @click="copyToken">复制</button>
        <button class="btn btn--ghost btn--small" @click="issued = null">我已记下</button>
      </div>
    </div>

    <div class="panel filters">
      <label class="field">
        <span class="field__label">用户名</span>
        <input v-model="username" @keyup.enter="load" />
      </label>
      <label class="field">
        <span class="field__label">角色</span>
        <select v-model="role" @change="load">
          <option value="">全部</option>
          <option value="user">user</option>
          <option value="admin">admin</option>
        </select>
      </label>
      <label class="field">
        <span class="field__label">状态</span>
        <select v-model="isActive" @change="load">
          <option value="">全部</option>
          <option value="true">启用</option>
          <option value="false">停用</option>
        </select>
      </label>
      <button class="btn btn--ghost btn--small" @click="load">刷新</button>
    </div>

    <div class="panel">
      <p v-if="loading" class="empty">加载中…</p>
      <table v-else class="table">
        <thead>
          <tr>
            <th>#</th>
            <th>用户名</th>
            <th>显示名</th>
            <th>角色</th>
            <th>状态</th>
            <th>邮箱</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="user in users" :key="user.id">
            <td class="num">{{ user.id }}</td>
            <td class="num">{{ user.username }}</td>
            <td>{{ user.display_name }}</td>
            <td><span class="tag" :class="user.role === 'admin' ? 'tag--live' : ''">{{ user.role }}</span></td>
            <td>
              <span class="tag" :class="user.is_active ? '' : 'tag--rejected'">
                {{ user.is_active ? '启用' : '停用' }}
              </span>
            </td>
            <td class="num dim">
              {{ user.email ?? '—' }}
              <span v-if="user.email && !user.email_verified" class="tag">未验证</span>
            </td>
            <td class="actions">
              <button
                v-if="user.role === 'user'"
                class="btn btn--ghost btn--small"
                @click="patch(user, { role: 'admin' })"
              >
                提权
              </button>
              <button
                v-else
                class="btn btn--ghost btn--small"
                @click="patch(user, { role: 'user' })"
              >
                降权
              </button>
              <button
                v-if="user.is_active"
                class="btn btn--danger btn--small"
                @click="patch(user, { is_active: false })"
              >
                停用
              </button>
              <button
                v-else
                class="btn btn--ghost btn--small"
                @click="patch(user, { is_active: true })"
              >
                启用
              </button>
              <button
                v-if="user.is_active"
                class="btn btn--ghost btn--small"
                @click="issueToken(user)"
              >
                重置令牌
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
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
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 14px;
  align-items: end;
}

.token {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  border-color: var(--red);
}

.token__title {
  font-size: 14px;
  letter-spacing: 0.06em;
}

.token__lead {
  margin: 0;
  font-size: 13px;
}

.token__value {
  padding: 10px 12px;
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: var(--radius-surface);
  overflow-wrap: anywhere;
  font-size: 13px;
  color: var(--red-hi);
}

.actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
</style>
