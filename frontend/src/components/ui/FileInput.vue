<script setup lang="ts">
/**
 * 文件选择。
 *
 * ## 为什么要包一层
 *
 * 原生 `<input type="file">` 的外观由浏览器绘制，暗色界面上会冒出一个浅色的
 * "选择文件"按钮和一段系统字体的文件名 —— 与其他控件格格不入，且样式基本管不到。
 * 这里把真实输入框藏起来（保留语义与键盘可达），另画一个与按钮同族的触发器，
 * 并显示选中的文件名与体积。
 *
 * ## 为什么不用 `<label>` 包住 `<input>`
 *
 * 那种写法会让点标签任意位置都打开文件对话框，看起来更方便，代价是**键盘焦点会
 * 落在隐藏的输入框上而不是可见的按钮上** —— 焦点样式画不出来，用户不知道自己在哪。
 * 所以这里用一个真按钮，点击时转交给输入框。
 */
import { computed, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 已选文件；null 表示还没选 */
    modelValue: File | null
    /** 传给原生输入框，例如 `.zip` */
    accept?: string
    /** 触发器上的文字 */
    label?: string
    disabled?: boolean
  }>(),
  { accept: undefined, label: '选择文件', disabled: false },
)

const emit = defineEmits<{ 'update:modelValue': [value: File | null] }>()

const input = ref<HTMLInputElement | null>(null)

const sizeText = computed(() => {
  const file = props.modelValue
  if (!file) return ''
  if (file.size < 1024) return `${file.size} B`
  if (file.size < 1024 * 1024) return `${(file.size / 1024).toFixed(1)} KB`
  return `${(file.size / 1024 / 1024).toFixed(1)} MB`
})

function open(): void {
  if (props.disabled) return
  input.value?.click()
}

function onChange(event: Event): void {
  const target = event.target as HTMLInputElement
  emit('update:modelValue', target.files?.[0] ?? null)
  // 清掉原生输入框里的值：不清的话，选同一个文件不会再触发 change，
  // 用户会以为"点了没反应"
  target.value = ''
}
</script>

<template>
  <div class="file">
    <input
      ref="input"
      class="file__input"
      type="file"
      :accept="accept"
      :disabled="disabled"
      @change="onChange"
    />

    <button
      class="btn btn--ghost btn--control"
      type="button"
      :disabled="disabled"
      @click="open"
    >
      {{ label }}
    </button>

    <span v-if="modelValue" class="file__picked">
      <span class="file__name mono">{{ modelValue.name }}</span>
      <span class="file__size num dim">{{ sizeText }}</span>
    </span>
    <span v-else class="file__empty dim">未选择文件</span>
  </div>
</template>

<style scoped>
.file {
  /* 定位上下文：隐藏的输入框靠它把自己关在行内，见下面 .file__input */
  position: relative;
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

/*
  藏起来但**保留键盘可达**：不能用 display:none 或 visibility:hidden，
  那会让它从可聚焦序列里消失。用 clip-path 保持它在无障碍树里。

  外面必须有定位祖先：否则它的包含块是初始包含块，而 `position: absolute` 的元素
  不受任何祖先的 overflow 裁剪，会跑到文档层面把整页撑高。
*/
.file__input {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  border: 0;
  clip-path: inset(50%);
  overflow: hidden;
}

.file__picked {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

/* 文件名可能很长，占满剩余宽度并截断 */
.file__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12.5px;
}

.file__size,
.file__empty {
  flex: none;
  font-size: 12.5px;
}
</style>
