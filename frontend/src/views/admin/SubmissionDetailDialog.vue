<script setup lang="ts">
/**
 * 提交详情对话框。
 *
 * 用原生 `<dialog>` + `showModal()`，而不是自己搓一个遮罩层：顶层渲染（不用跟
 * z-index 打架）、焦点陷阱、`Esc` 关闭、`::backdrop` 全是白送的。自己实现这四样
 * 很容易漏掉焦点陷阱，而漏掉之后键盘用户会 tab 到背后的页面上。
 *
 * 对话框**始终在 DOM 里**（只是没打开），因为 `showModal()` 需要一个已挂载的元素。
 * 内容用 `v-if` 控制。
 */
import { computed, onMounted, ref, watch } from 'vue'

import { attachmentUrl } from '@/api/submissions'
import { payloadDisplay, payloadJson, statusLabel, statusTone } from '@/domain/submission'
import type { Submission } from '@/types/api'

const props = defineProps<{ submission: Submission | null }>()
const emit = defineEmits<{ close: [] }>()

const dialog = ref<HTMLDialogElement | null>(null)

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

/**
 * 让对话框的开合跟上 `submission`。
 *
 * `flush: 'post'` 是必须的：`showModal()` 要求元素已挂载，而模板 ref 在渲染后才
 * 绑上。
 */
function sync(value: Submission | null): void {
  const element = dialog.value
  if (!element) return
  if (value && !element.open) element.showModal()
  else if (!value && element.open) element.close()
}

watch(() => props.submission, sync, { flush: 'post', immediate: true })

// `immediate` 那一次跑在模板 ref 绑定之前（那时 dialog 还是 null），所以挂载后
// 再补一次。少了它，"挂载时就带着一条提交"的用法会静默地不打开。
onMounted(() => sync(props.submission))

/** 点遮罩关闭。`<dialog>` 默认不这么做，但用户的预期是点了外面就该关 */
function onBackdropClick(event: MouseEvent): void {
  if (event.target === dialog.value) emit('close')
}
</script>

<template>
  <!-- `@close` 覆盖 Esc 与 close() 两条路径，统一往上抛 -->
  <dialog
    ref="dialog"
    class="detail"
    aria-labelledby="detail-title"
    @close="emit('close')"
    @click="onBackdropClick"
  >
    <!--
      布局放在这层内层容器上，而不是 `<dialog>` 本身：在 dialog 上写 `display`
      会盖掉浏览器默认的 `dialog:not([open]) { display: none }`，让关闭状态的
      对话框依然占位显示。
    -->
    <div v-if="submission" class="detail__body">
      <header class="detail__head">
        <h2 id="detail-title" class="detail__title">提交 #{{ submission.id }}</h2>
        <button class="btn btn--ghost btn--small" type="button" @click="emit('close')">
          关闭
        </button>
      </header>

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
    </div>
  </dialog>
</template>

<style scoped>
/*
  **这个规则块里绝不能出现 `display`。**
  作者样式里的 `display` 会盖掉浏览器默认的 `dialog:not([open]) { display: none }`
  —— 于是关闭状态的对话框依然会被渲染，页面上凭空多出一块空卡片。
  布局交给内层的 .detail__body，这里只管外观与尺寸。
*/
.detail {
  width: min(720px, 92vw);
  max-height: 86vh;
  padding: 20px 22px 24px;
  overflow: auto;
  background: var(--panel);
  border: 1px solid var(--line-strong);
  border-radius: var(--radius-surface);
  color: var(--bone);
}

.detail__body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.detail::backdrop {
  background: var(--overlay);
}

.detail__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.detail__title {
  font-size: 16px;
}

/* 元信息两列排布：窄屏自动落成一列 */
.detail__meta {
  margin: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 10px 20px;
  padding: 14px 0;
  border-top: 1px solid var(--line);
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
