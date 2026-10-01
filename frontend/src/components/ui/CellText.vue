<script setup lang="ts">
/**
 * 表格里的文本单元格：定宽、超长截断。
 *
 * 提交内容是一段长度不受控的 JSON，直接铺进表格会把那一列撑到几千像素，整张表
 * 因此失去列宽、没法扫读。截断把这件事钉死，代价是看不全 —— 完整内容由**点击整行
 * 弹出的详情对话框**兜住，所以这里刻意不做任何展开交互。
 *
 * 连 `title` 都不给：原生悬浮提示出现得慢、样式也跟不上，而详情对话框已经提供了
 * 完整内容。多一个半吊子的入口只会让人犹豫该用哪个。
 */
defineProps<{ text: string }>()
</script>

<template>
  <span class="cell">{{ text }}</span>
</template>

<style scoped>
.cell {
  /* 这三条就是截断本身 */
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  /*
    `min-width: 0` 是给"作为 flex 子项"准备的：flex 子项默认 min-width:auto，
    没有它就不会收缩到内容宽度以下，省略号永远不出现。放在这里而不是让每个调用方
    自己写 —— 忘了写就静默失效，而失效的样子（文本溢出而不是截断）很容易被忽略。

    字号与颜色一律继承：各列的字号本来就不一样，组件不该替它们决定。
    字体族统一等宽 —— 这些格子里装的都是标识、JSON 这类机器值。
  */
  min-width: 0;
  font-family: var(--mono);
}
</style>
