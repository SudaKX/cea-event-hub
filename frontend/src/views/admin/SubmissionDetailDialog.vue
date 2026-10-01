<script setup lang="ts">
/**
 * 提交详情对话框。
 *
 * `<dialog>` 的开合、焦点陷阱、遮罩点击这些接线都在 `Modal` 里，这里只负责内容。
 */
import { computed } from 'vue'

import { attachmentUrl } from '@/api/submissions'
import Modal from '@/components/ui/Modal.vue'
import { payloadDisplay, payloadJson, statusLabel, statusTone } from '@/domain/submission'
import type { Submission } from '@/types/api'

const props = defineProps<{ submission: Submission | null }>()
const emit = defineEmits<{ close: [] }>()

const display = computed(() =>
  props.submission ? payloadDisplay(props.submission.payload) : null,
)
const json = computed(() =>
  props.submission ? payloadJson(props.submission.payload) : '',
)

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
</script>

<template>
  <Modal
    class="detail-modal"
    :open="submission !== null"
    :title="submission ? `提交 #${submission.id}` : ''"
    @close="emit('close')"
  >
    <template v-if="submission">
      <dl class="detail__meta">
        <div class="detail__pair">
          <dt>提交者</dt>
          <dd class="num">
            {{ submission.submitter }}
            <span v-if="!submission.from_authenticated_user" class="tag">匿名</span>
          </dd>
        </div>
        <div class="detail__pair">
          <dt>分类</dt>
          <dd class="num">{{ submission.kind }}</dd>
        </div>
        <div class="detail__pair">
          <dt>状态</dt>
          <dd>
            <span class="tag" :class="`tag--${statusTone(submission.status)}`">
              {{ statusLabel(submission.status) }}
            </span>
          </dd>
        </div>
        <div class="detail__pair">
          <dt>时间</dt>
          <dd class="num">{{ new Date(submission.created_at).toLocaleString('zh-CN') }}</dd>
        </div>
      </dl>

      <!-- $display 是导读，原始数据仍然完整给出 -->
      <p v-if="display" class="detail__display">{{ display }}</p>

      <section class="detail__section">
        <h3 class="detail__label">内容</h3>
        <pre class="detail__json">{{ json }}</pre>
      </section>

      <section class="detail__section">
        <h3 class="detail__label">附件</h3>
        <p v-if="submission.files.length === 0" class="dim detail__none">没有附件</p>
        <ul v-else class="detail__files">
          <li v-for="file in submission.files" :key="file.id" class="detail__file">
            <a class="mono grow" :href="attachmentUrl(submission.id, file.id)">
              {{ file.original_name }}
            </a>
            <span class="num dim">{{ formatSize(file.size_bytes) }}</span>
          </li>
        </ul>
      </section>
    </template>
  </Modal>
</template>

<style scoped>
/*
  外壳（尺寸、边框、遮罩、开合）都在 Modal 里，这里只排内容。
  元信息两列排布：窄屏自动落成一列
*/
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

.detail__display {
  margin: 0;
  padding: 10px 12px;
  border-left: 2px solid var(--red);
  background: var(--red-soft);
  border-radius: var(--radius-control);
  font-size: 13px;
  line-height: 1.7;
}

.detail__section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.detail__label {
  font: 600 12px/1 var(--mono);
  letter-spacing: 0.08em;
  color: var(--mute);
}

.detail__json {
  margin: 0;
  padding: 12px;
  max-height: 320px;
  overflow: auto;
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: var(--radius-surface);
  font-size: 12.5px;
  line-height: 1.6;
  /* JSON 里有长串时按词换行很难看，直接按字符断 */
  white-space: pre-wrap;
  word-break: break-all;
}

.detail__none {
  margin: 0;
  font-size: 13px;
}

.detail__files {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.detail__file {
  display: flex;
  align-items: baseline;
  gap: 12px;
  font-size: 13px;
}
</style>
