<script setup lang="ts">
/**
 * 提交者：标识 + 互斥的状态标记。
 *
 * **抽出来是因为漏过一次。** 同一段逻辑原先写在提交列表与提交详情两处，给已删除
 * 账号补「已删除」标记时只改了列表，详情里那条提交仍然顶着「匿名」—— 而两处显示的
 * 本该是同一件事。现在只有一份实现。
 *
 * 三条规则：
 *
 * 1. **"已删除"与"匿名"互斥。** 前者是登录用户交的、只是那个账号不在了；后者是访客
 *    交的。两者都会让账号归属为空，混为一谈会让管理员误以为那条提交是访客交的。
 * 2. **显示名优先，没有就回落到标识。** 账号已删时后端刻意不给显示名（没有更好的
 *    名字），标识因此露出来 —— 它仍然有用，正是筛选参数要用的值。
 * 3. **标识走 `CellText` 截断。** 匿名的 `a:<uuid>` 有 38 个字符，远超列宽；直接插值
 *    只会被硬裁，没有省略号。而标记不能跟着被截 —— 它才是真正要看的信息。
 */
import CellText from '@/components/ui/CellText.vue'
import type { Submission } from '@/types/api'

defineProps<{ submission: Submission }>()
</script>

<template>
  <!--
    根元素是 `div` 而不是 `span`：它要落在一个 `<td>` 里，而给 `<td>` 直接加
    `display: flex` 会让它不再是 table-cell、行分隔线跟着断掉（且不报任何错）。
    `SubmissionsView.spec.ts` 有一条结构断言守着这件事。
  -->
  <div class="submitter">
    <CellText
      class="submitter__id"
      :text="submission.submitter_display || submission.submitter"
    />
    <span v-if="submission.submitter_deleted" class="tag tag--deleted">已删除</span>
    <span v-else-if="!submission.from_authenticated_user" class="tag">匿名</span>
  </div>
</template>

<style scoped>
.submitter {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

/*
  标识自己负责截断 —— 它可能很长（匿名的 `a:<uuid>` 有 38 个字符）。
  `flex: 1` 让它占满剩余空间并允许收缩，`min-width: 0` 则解开 flex 项默认的
  "不小于内容宽度"，否则截断不会发生。
*/
.submitter__id {
  flex: 1;
  min-width: 0;
}

/* 标记不参与收缩，也不会被截 —— 它才是这一列真正要看的信息 */
.submitter > .tag {
  flex: none;
}
</style>
