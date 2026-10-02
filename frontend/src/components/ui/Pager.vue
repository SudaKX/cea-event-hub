<script setup lang="ts">
/**
 * 分页控件。
 *
 * 抽成组件而不是每个列表各写一份：分页有一堆容易漏的边角（当前页越界、每页条数
 * 变化、只有一页时要不要显示、空列表时显示什么），散在各处必然漂移出不一致。
 *
 * **它不自己取数据。** 只负责把"第几页""每页多少条"报给调用方 —— 数据的所有权
 * 在调用方，这里不该有第二份真相。
 *
 * **也不要往里塞与分页无关的操作。** 曾经开过一个 `actions` 插槽放批量按钮，
 * 结果是这一行挤成一片，而且"作用于本页选中项"与"作用于跨页队列"两类操作混在
 * 一起、看不出区别。列表自己的操作栏应该由列表自己渲染 —— 它才知道该怎么分行。
 */
import { computed } from 'vue'

import Select from './Select.vue'
import type { SelectOption } from './Select.vue'

const props = withDefaults(
  defineProps<{
    page: number
    pageSize: number
    total: number
    /** 每页条数的可选项。传空数组就不显示这个选择器 */
    pageSizeOptions?: number[]
    /** 分页器右侧的单位，例如"条" */
    unit?: string
  }>(),
  {
    // 10 是给"扫一眼就够"的列表准备的：管理端多数时候不需要一屏 20 条
    pageSizeOptions: () => [10, 20, 50, 100],
    unit: '条',
  },
)

const emit = defineEmits<{
  'update:page': [value: number]
  'update:pageSize': [value: number]
}>()

const pageCount = computed(() =>
  Math.max(1, Math.ceil(props.total / Math.max(1, props.pageSize))),
)

/** 当前页覆盖的区间。空列表时为 0–0，避免出现"第 1–0 条" */
const from = computed(() =>
  props.total === 0 ? 0 : (props.page - 1) * props.pageSize + 1,
)
const to = computed(() => Math.min(props.total, props.page * props.pageSize))

const sizeOptions = computed<SelectOption[]>(() =>
  props.pageSizeOptions.map((size) => ({ value: String(size), label: `${size} / 页` })),
)

/** 每页条数变了必须回到第一页，否则会停在一个可能已不存在的页码上 */
function onSizeChange(value: string): void {
  emit('update:pageSize', Number(value))
  emit('update:page', 1)
}

function go(target: number): void {
  const next = Math.min(Math.max(1, target), pageCount.value)
  if (next !== props.page) emit('update:page', next)
}
</script>

<template>
  <!-- 空列表时整块不显示：上面已经有"还没有提交"之类的空状态了 -->
  <nav v-if="total > 0" class="pager" aria-label="分页">
    <span class="pager__range num dim">
      第 {{ from }}–{{ to }} {{ unit }} · 共 {{ total }} {{ unit }}
    </span>

    <div class="pager__nav">
      <button
        class="btn btn--ghost btn--small"
        type="button"
        :disabled="page <= 1"
        aria-label="第一页"
        @click="go(1)"
      >
        «
      </button>
      <button
        class="btn btn--ghost btn--small"
        type="button"
        :disabled="page <= 1"
        @click="go(page - 1)"
      >
        上一页
      </button>

      <span class="pager__position num">{{ page }} / {{ pageCount }}</span>

      <button
        class="btn btn--ghost btn--small"
        type="button"
        :disabled="page >= pageCount"
        @click="go(page + 1)"
      >
        下一页
      </button>
      <button
        class="btn btn--ghost btn--small"
        type="button"
        :disabled="page >= pageCount"
        aria-label="最后一页"
        @click="go(pageCount)"
      >
        »
      </button>
    </div>

    <Select
      v-if="sizeOptions.length > 0"
      class="pager__size"
      :model-value="String(pageSize)"
      :options="sizeOptions"
      aria-label="每页条数"
      @update:model-value="onSizeChange"
    />
  </nav>
</template>

<style scoped>
.pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  padding: 14px;
  border-top: 1px solid var(--line);
}

.pager__range {
  font-size: 12.5px;
}

.pager__nav {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pager__position {
  min-width: 56px;
  text-align: center;
  font-size: 12.5px;
  color: var(--mute);
}

.pager__size {
  width: 108px;
}
</style>
