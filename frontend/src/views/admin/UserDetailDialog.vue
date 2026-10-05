<script setup lang="ts">
/**
 * 用户详情。双击列表行打开。
 *
 * 外壳（开合、焦点陷阱、遮罩点击）都在 `Modal` 里，这里只负责内容。
 *
 * **删除放在这里而不是批量面板里**：它一次只对一个人有意义 —— 与"重置令牌"同一个
 * 理由（见 `UsersView` 里那句注释）。这里只发事件，确认与请求由视图负责：视图拥有
 * 数据，对话框不该自己去改。
 */
import Modal from '@/components/ui/Modal.vue'
import type { UserAdmin } from '@/types/api'

defineProps<{ user: UserAdmin | null }>()
const emit = defineEmits<{ close: []; delete: [user: UserAdmin] }>()
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

      <!--
        删除是**独立的危险区域**，不与众操作并排：它与"停用"回答的是不同的问题
        （停用可逆、提交署名完好；删除不可逆、署名此后只剩编号），因此要让人在点之前
        先读到代价。
      -->
      <section class="detail__danger">
        <h3 class="detail__danger-title">删除账号</h3>
        <p class="dim detail__note">
          账号会被彻底移除，<strong>无法恢复</strong>，他也不能再登录。
          <strong>他提交过的内容会保留</strong> —— 那是社团收集的数据，与账号是两件事；
          但署名此后只剩一个编号，界面上会标出「已删除」，谁交的再也查不出来。
        </p>
        <div class="detail__danger-actions">
          <button
            class="btn btn--danger btn--small"
            type="button"
            @click="emit('delete', user)"
          >
            删除账号
          </button>
        </div>
      </section>
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

/* 与其他操作拉开距离，并让"这是另一回事"在视觉上先成立 */
.detail__danger {
  margin-top: 18px;
  padding-top: 16px;
  border-top: 1px solid var(--line);
}

.detail__danger-title {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--red-hi);
}

.detail__danger-actions {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}
</style>
