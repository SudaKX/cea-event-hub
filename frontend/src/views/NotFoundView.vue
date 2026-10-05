<script setup lang="ts">
/**
 * 未找到页面。
 *
 * 视觉沿用既有海报的暗色 + 点阵语言：左侧点阵字，右侧说明与出口。
 * 与活动页共用同一套设计令牌，因此观感一致。
 */
import DotMatrix from '@/components/ui/DotMatrix.vue'
</script>

<template>
  <main class="nf">
    <div class="nf__row">
      <div class="nf__art">
        <DotMatrix text="404" sub="NOT FOUND" cursor />
      </div>

      <div class="nf__col">
        <h1 class="nf__title">页面<span class="dim">/</span><em>不存在</em></h1>
        <p class="mute nf__lead">
          这个地址没有对应的活动，或者活动还没有发布。<br />
          如果链接来自别人分享，可能是活动已被归档。
        </p>

        <div class="nf__actions">
          <!--
            主出口是首页：404 谁都会遇到，而管理台只对管理员开放 —— 把一个多数人
            进不去的地方当主出口，等于让 404 变成第二道墙。管理员在首页也有管理台
            入口，因此这里不损失什么。
          -->
          <RouterLink class="btn btn--primary" to="/">返回首页</RouterLink>
          <RouterLink class="btn btn--ghost" to="/login">登录</RouterLink>
        </div>
      </div>
    </div>
  </main>
</template>

<style scoped>
.nf {
  min-height: 100%;
  display: grid;
  place-items: center;
  padding: 40px 28px;
}

/* 左图右文：与海报的版式一致 */
.nf__row {
  display: flex;
  align-items: center;
  gap: var(--gap-rail);
}

.nf__art {
  flex: none;
  width: clamp(150px, 24vw, 260px);
}

.nf__col {
  min-width: 0;
  width: clamp(220px, 34vw, 380px);
}

.nf__title {
  font-size: clamp(22px, 3.4vw, 34px);
}

.nf__title em {
  font-style: normal;
  color: var(--red-hi);
}

.nf__lead {
  margin: 14px 0 0;
  font-size: clamp(13px, 1.2vw, 14px);
  line-height: 1.8;
}

.nf__actions {
  display: flex;
  gap: 10px;
  margin-top: 22px;
}

@media (max-width: 560px) {
  .nf__row {
    flex-direction: column;
    gap: 20px;
  }

  .nf__art {
    width: 180px;
  }

  .nf__col {
    width: 100%;
  }
}
</style>
