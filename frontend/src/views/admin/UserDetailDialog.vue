<script setup lang="ts">
/**
 * 用户详情。双击列表行打开。
 *
 * 外壳（开合、焦点陷阱、遮罩点击）都在 `Modal` 里，这里只负责内容。
 */
import Modal from '@/components/ui/Modal.vue'
import type { UserAdmin } from '@/types/api'

defineProps<{ user: UserAdmin | null }>()
const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <Modal
    class="user-modal"
    :open="user !== null"
    :title="user ? `${user.display_name} · ${user.username}` : ''"
    @close="emit('close')"
  >
    <template v-if="user">
      <dl class="detail__meta">
        <div class="detail__pair">
          <dt>编号</dt>
          <dd class="num">{{ user.id }}</dd>
        </div>
        <div class="detail__pair">
          <dt>角色</dt>
          <dd>
            <span class="tag" :class="user.role === 'admin' ? 'tag--live' : ''">
              {{ user.role }}
            </span>
          </dd>
        </div>
        <div class="detail__pair">
          <dt>状态</dt>
          <dd>
            <span class="tag" :class="user.is_active ? '' : 'tag--off'">
              {{ user.is_active ? '启用' : '停用' }}
            </span>
          </dd>
        </div>
        <div class="detail__pair">
          <dt>邮箱</dt>
          <dd class="num">
            {{ user.email ?? '—' }}
            <span v-if="user.email && !user.email_verified" class="tag">未验证</span>
          </dd>
        </div>
        <div class="detail__pair">
          <dt>注册于</dt>
          <dd class="num">{{ new Date(user.created_at).toLocaleString('zh-CN') }}</dd>
        </div>
      </dl>

      <p class="dim detail__note">
        提权、降权与停用都会<strong>立即吊销该用户的全部会话</strong>：不吊销的话，
        降级后的用户在旧会话里仍持有管理权限，而停用只是"下次登录才生效"。
      </p>
    </template>
  </Modal>
</template>

<style scoped>
/* 元信息两列排布：窄屏自动落成一列 */
.detail__meta {
  margin: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 10px 20px;
  padding: 0 0 14px;
  border-bottom: 1px solid var(--line);
}

.detail__pair {
  display: flex;
  gap: 10px;
  align-items: baseline;
  font-size: 13px;
}

.detail__pair dt {
  flex: none;
  width: 56px;
  color: var(--mute);
  font-size: 12px;
}

.detail__pair dd {
  margin: 0;
  min-width: 0;
  overflow-wrap: anywhere;
}

.detail__note {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.8;
}

.detail__note strong {
  color: var(--bone);
}
</style>
