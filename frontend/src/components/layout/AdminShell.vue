<script setup lang="ts">
/**
 * 管理台外壳。
 *
 * 三个入口：活动、提交、用户。用户入口只对管理员可见 —— 非管理员既看不到它，
 * 直接访问也会被路由守卫挡回。
 */
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { useSignOut } from '@/composables/useSignOut'

const auth = useAuthStore()
const { signOut } = useSignOut()
const router = useRouter()
const route = useRoute()

const navItems = computed(() => {
  const items = [
    { name: 'admin-events', label: '活动', hint: '创建、发布与投放内容' },
    { name: 'admin-submissions', label: '提交', hint: '审核与清理提交' },
  ]
  if (auth.isAdmin) {
    // 邀请与用户都管的是账号与准入门槛，越权访问的影响面比活动与提交更大，
    // 因此与用户管理同档：**仅管理员可见**
    items.push({ name: 'admin-invitations', label: '邀请', hint: '准入与平台开关' })
    items.push({ name: 'admin-users', label: '用户', hint: '角色与状态管理' })
  }
  return items
})

/**
 * 这一项是否处于选中态。
 *
 * **不能用 `router-link-active`。** 那个类按**路由记录**判定：当前路由的 matched
 * 里要包含链接指向的那条记录。而活动详情（`events/:eventId`）在路由表里是活动
 * 列表（`events`）的**兄弟**而不是子路由，所以进详情页时 matched 里没有 `events`
 * 那条记录 —— 于是"活动"这一项的选中态消失。
 *
 * 这里改成按**解析出来的路径**判断：当前 URL 等于它、或落在它下面，就算选中。
 * 这与使用者对侧边栏的预期一致（"我在活动这一块里"），也不必为了选中态去把路由
 * 表扭成嵌套结构 —— 那需要给分组记录配一个什么都不渲染的组件，得不偿失。
 */
function isActive(name: string): boolean {
  const target = router.resolve({ name }).path
  return route.path === target || route.path.startsWith(`${target}/`)
}

async function onSignOut(): Promise<void> {
  /*
    走共用的动作，而不是自己 `auth.signOut()` + 跳转 —— 那样"登出之后去哪"就有了两份
    答案，而两份答案迟早会不一致。个人中心用的是同一个 composable。
    `useSignOut.spec.ts` 里有一条结构性断言守着这件事。
  */
  await signOut()
}
</script>

<template>
  <div class="shell">
    <aside class="shell__side">
      <div class="shell__brand">
        <span class="shell__brand-mark">CEA</span>
        <span class="dim">/</span>
        <span class="shell__brand-sub">活动平台</span>
      </div>

      <nav class="shell__nav">
        <RouterLink
          v-for="item in navItems"
          :key="item.name"
          class="shell__link"
          :class="{ 'shell__link--active': isActive(item.name) }"
          :to="{ name: item.name }"
        >
          <span class="shell__link-label">{{ item.label }}</span>
          <span class="shell__link-hint dim">{{ item.hint }}</span>
        </RouterLink>
      </nav>

      <div class="shell__foot">
        <div class="shell__who">
          <span class="mono">{{ auth.user?.display_name ?? '—' }}</span>
          <span class="tag">{{ auth.user?.role ?? '匿名' }}</span>
        </div>
        <!--
          回公开站点的出口。管理台是**另一套版式**（固定视口、没有站点页头），进去
          之后浏览器"后退"未必能一步回到首页 —— 尤其从书签直接进来时。所以这里给
          一个明确的出口，与首页那个"管理台"入口正好互为镜像。
        -->
        <RouterLink class="btn btn--ghost btn--small" :to="{ name: 'home' }">
          返回主页
        </RouterLink>
        <!--
          退出用**强调色**：它与上面那个"返回主页"不是一类动作 —— 一个是导航，一个是
          结束会话。两个都做成 ghost 时它们看起来同样轻，而退出是会把人踢出登录态的
          那一个，值得显眼。
        -->
        <button class="btn btn--danger btn--small" type="button" @click="onSignOut">
          退出登录
        </button>
      </div>
    </aside>

    <main class="shell__main">
      <!--
        多这一层是为了让**内容滚而不是整页滚**：`.shell__main` 自己裁掉溢出，
        滚动条出现在这一层里，左侧导航因此始终停在原地。
        内边距也放在这一层 —— 放在外面的话，滚动条会跑到视口最右边，
        与内容之间隔着一段空白。
      -->
      <div class="shell__scroll">
        <RouterView />
      </div>
    </main>
  </div>
</template>

<style scoped>
/*
  固定视口高度，整页不滚。

  `height: 100%` + `overflow: hidden` 取代了原来的 `min-height: 100%` —— 后者让
  外壳跟着内容一起长高，于是滚动发生在**页面**上：左侧导航会跟着一起滚走，右侧
  再多出滚动条。

  **`grid-template-rows` 不能省。** 只定义列的话那一行是隐式的 `auto` 行，会跟着
  内容长高：格子里的 `.shell__scroll` 于是也变成内容那么高（比如 1000px），再被
  外壳的高度裁掉 —— 表现是内容被截断**而且滚不动**，因为滚动层自己就比可视区高。
  `minmax(0, 1fr)` 把这一行钉死在外壳的高度上，内层才真的能滚。
*/
.shell {
  display: grid;
  grid-template-columns: 232px 1fr;
  grid-template-rows: minmax(0, 1fr);
  height: 100%;
  overflow: hidden;
}

/*
  左侧不滚动。它装的是品牌、三个导航项和一行用户信息，正常窗口高度下绰绰有余；
  裁掉溢出比让它自己滚更符合"导航始终在同一个位置"的预期。
*/
.shell__side {
  display: flex;
  flex-direction: column;
  gap: 22px;
  padding: 22px 18px;
  border-right: 1px solid var(--line);
  background: var(--panel);
  overflow: hidden;
}

.shell__brand {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font: 700 17px/1 var(--mono);
  letter-spacing: 0.06em;
}

.shell__brand-mark {
  color: var(--red-hi);
}

.shell__brand-sub {
  font-size: 12px;
  color: var(--mute);
  letter-spacing: 0.1em;
}

.shell__nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
}

.shell__link {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 9px 11px;
  border-radius: var(--radius-control);
  color: var(--bone);
  text-decoration: none;
  transition: background var(--transition-fast);
}

.shell__link:hover {
  background: rgba(255, 255, 255, 0.03);
  text-decoration: none;
}

/*
  当前项用左侧红条标记，而不是整块高亮 —— 更接近海报的克制感。

  **这条规则挂在自定义类上，不是 `router-link-active`** —— 后者按路由记录判定，
  认不出"活动详情属于活动"这件事。见脚本里 `isActive` 的说明。
*/
.shell__link--active {
  background: var(--red-soft);
  box-shadow: inset 2px 0 0 var(--red);
}

.shell__link-label {
  font: 600 14px/1.4 var(--sans);
}

.shell__link-hint {
  font-size: 11px;
}

.shell__foot {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 14px;
  border-top: 1px solid var(--line);
}

.shell__who {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

/*
  `min-height: 0` 不能少：grid 子项默认 `min-height: auto`，会被内容顶高，
  于是这一格跟着内容一起长，内层就再也滚不起来了。
*/
.shell__main {
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

/* 真正滚动的那一层 */
.shell__scroll {
  height: 100%;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 26px 28px 48px;
}

@media (max-width: 720px) {
  /*
    窄屏改成上下堆叠：导航占满一屏宽，主区在下面。这时内层再滚会变成两个窄条，
    所以退回整页滚动 —— 布局换了，滚动的归属也该跟着换。
  */
  .shell {
    grid-template-columns: 1fr;
    /* 上下堆叠是两行，行高各自按内容算，整页滚动 */
    grid-template-rows: auto;
    height: auto;
    min-height: 100%;
    overflow: visible;
  }

  .shell__side {
    border-right: 0;
    border-bottom: 1px solid var(--line);
    overflow: visible;
  }

  .shell__main {
    overflow: visible;
  }

  .shell__scroll {
    height: auto;
    overflow: visible;
  }
}
</style>
