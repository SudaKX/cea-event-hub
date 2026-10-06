/**
 * 承载活动内容的 iframe 的沙箱令牌集合 —— **唯一真源**。
 *
 * 生产宿主（`event/EventView.vue`）与开发调试台（`develop/DevelopView.vue`）共用
 * 这一份。调试台存在的意义就是让本地跑的是与线上同一套语义，而沙箱属性是这套语义
 * 的一部分 —— 两处各写一份的话，往生产那份加一个令牌却忘了调试台，本地验证就会在
 * 一个**更宽松**的环境里通过，而那正是最坏的一种"验证通过"。
 *
 * ## 绝不含 `allow-same-origin`
 *
 * 同源 iframe 一旦拿到这个令牌，沙箱等于没有：它能访问 `parent.document`、读到宿主
 * 内存里的状态，甚至把自己身上的 sandbox 属性摘掉。此时"代理"只是一种礼貌约定，
 * 不是安全边界。
 *
 * 其余令牌的用途，以及刻意**不给**的两个（`allow-top-navigation`、
 * `allow-popups-to-escape-sandbox`），见 `docs/bridge-protocol.md`。
 *
 * 这份声明由 `src/views/event/sandbox.spec.ts` 逐条守着 —— 它读源码文本断言，
 * 因为"活动页碰不到宿主"是浏览器行为，在 Node 里断言不了，而**保证这一点的配置**
 * 可以断言，也必须断言：往列表里加一个令牌会让整个隔离机制静默失效，其它测试全绿。
 */
export const SANDBOX_TOKENS = [
  'allow-scripts',
  'allow-forms',
  'allow-modals',
  'allow-popups',
] as const
