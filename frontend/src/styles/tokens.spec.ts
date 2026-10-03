/**
 * 任务 12.2：设计令牌与规格逐一对应。
 *
 * 直接读 CSS 源文件而不是 getComputedStyle：变量可能被别处覆盖，
 * 而这里要断言的是"定义本身与规格一致"。
 */
import { readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

// 用 cwd 相对路径而不是 import.meta.url：happy-dom 环境下后者不是 file: URL，
// fileURLToPath 会直接抛错。vitest 的工作目录就是 frontend/。
const stylesDir = resolve(process.cwd(), 'src/styles')
const read = (name: string) => readFileSync(resolve(stylesDir, name), 'utf-8')
const tokensCss = read('tokens.css')
const baseCss = read('base.css')
const componentsCss = read('components.css')
const allCss = `${tokensCss}\n${baseCss}\n${componentsCss}`

const srcDir = resolve(process.cwd(), 'src')

/**
 * 取出匹配到的规则块，并**剥掉注释**再返回。
 *
 * 剥注释不能省：注释里常常写着属性名（"`min-width: 0` 是给 flex 准备的"），
 * 用 toContain 直接匹配会命中注释而不是声明 —— 把声明删掉测试依然全绿。
 * 变异测试真的放过它一次。
 */
function ruleOf(source: string, selector: RegExp): string {
  const found = selector.exec(source)?.[0] ?? ''
  return found.replace(/\/\*[\s\S]*?\*\//g, '')
}

/** 递归收集所有 .vue / .ts 源码（排除测试文件） */
function collectSources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = resolve(dir, entry.name)
    if (entry.isDirectory()) return collectSources(full)
    if (!/\.(vue|ts)$/.test(entry.name)) return []
    if (/\.spec\.ts$/.test(entry.name)) return []
    return [readFileSync(full, 'utf-8')]
  })
}

/** 规格要求的取值，逐个对应 */
const REQUIRED_TOKENS: Record<string, string> = {
  '--bg': '#0b0b0d',
  '--panel': '#101014',
  '--bone': '#cfcac4',
  '--mute': '#8b8e97',
  '--dim': '#5f626b',
  '--red': '#d0202f',
  '--red-hi': '#ff4a55',
  '--line': 'rgba(255, 255, 255, 0.09)',
}

describe('设计令牌', () => {
  it.each(Object.entries(REQUIRED_TOKENS))('%s 取值为 %s', (name, value) => {
    const pattern = new RegExp(`${name}\\s*:\\s*${value.replace(/[()]/g, '\\$&')}\\s*;`)
    expect(tokensCss).toMatch(pattern)
  })

  it('定义了两套字体栈：等宽与无衬线', () => {
    expect(tokensCss).toMatch(/--mono\s*:[^;]*monospace/)
    expect(tokensCss).toMatch(/--sans\s*:[^;]*sans-serif/)
  })

  it('等宽栈以 ui-monospace 开头，无衬线栈包含中文字体回退', () => {
    const mono = /--mono\s*:\s*([^;]+);/.exec(tokensCss)?.[1] ?? ''
    expect(mono.trim().startsWith('ui-monospace')).toBe(true)

    const sans = /--sans\s*:\s*([^;]+);/.exec(tokensCss)?.[1] ?? ''
    expect(sans).toContain('Microsoft YaHei')
  })

  it('圆角只有两档', () => {
    expect(tokensCss).toMatch(/--radius-control\s*:\s*7px/)
    expect(tokensCss).toMatch(/--radius-surface\s*:\s*10px/)
  })
})

describe('样式约束', () => {
  it('不使用渐变', () => {
    // 渐变会破坏海报那种平涂的硬边质感
    expect(allCss).not.toMatch(/linear-gradient|radial-gradient|conic-gradient/)
  })

  it('过渡时长不超过 200ms', () => {
    const durations = [...allCss.matchAll(/(\d+)ms/g)].map((match) => Number(match[1]))
    // 允许 0.01ms 这类"关闭动效"的写法
    const real = durations.filter((value) => value > 1)
    expect(real.length).toBeGreaterThan(0)
    for (const value of real) expect(value).toBeLessThanOrEqual(200)
  })

  it('焦点样式为亮强调色 2px 实线且偏移 3px', () => {
    expect(baseCss).toMatch(/outline:\s*2px solid var\(--red-hi\)/)
    expect(baseCss).toMatch(/outline-offset:\s*3px/)
  })

  it('尊重系统的减少动效偏好', () => {
    expect(baseCss).toContain('prefers-reduced-motion')
  })

  it('数字与标识使用等宽字体', () => {
    expect(baseCss).toMatch(/\.num\s*\{[^}]*var\(--mono\)/)
    // 表格里的数字列同样走等宽，列宽才稳定
    expect(componentsCss).toMatch(/\.table[^{]*\.num[^{]*\{[^}]*var\(--mono\)/)
  })

  it('标题使用等宽字体', () => {
    expect(baseCss).toMatch(/h1,\s*h2,\s*h3\s*\{[^}]*var\(--mono\)/)
  })
})

describe('标签配色不留空档', () => {
  /*
    用了未定义的类名不会报错，只是**默默地没有样式** —— 页面上看不出异常，
    只有仔细比对才会发现某个标签"好像变淡了"。

    「停用」标签就这样失效过：提交状态改造时把 tag--rejected 改名为 tag--ignored，
    而 UsersView 还在用旧名字。所以这里逐个核对源码里出现的后缀。
  */
  it('源码里静态用到的 tag--* 都在 components.css 里有定义', () => {
    const defined = new Set(
      [...componentsCss.matchAll(/\.(tag--[a-z][a-z0-9-]*)/g)].map((m) => m[1]),
    )
    expect(defined.size).toBeGreaterThan(0)

    const used = new Set<string>()
    for (const source of collectSources(srcDir)) {
      for (const match of source.matchAll(/'tag--([a-z][a-z0-9-]*)'|"tag--([a-z][a-z0-9-]*)"|`tag--([a-z][a-z0-9-]*)`/g)) {
        used.add(`tag--${match[1] ?? match[2] ?? match[3]}`)
      }
    }

    const missing = [...used].filter((name) => !defined.has(name)).sort()
    expect(missing).toEqual([])
  })

  it('动态拼出来的状态后缀也都有定义', () => {
    // statusTone() 返回的词会拼进 `tag--${tone}`，静态扫描抓不到，所以单独核对
    const defined = new Set(
      [...componentsCss.matchAll(/\.(tag--[a-z][a-z0-9-]*)/g)].map((m) => m[1]),
    )
    const statusSource = readFileSync(resolve(srcDir, 'domain/submission.ts'), 'utf-8')
    const tones = [...statusSource.matchAll(/:\s*'([a-z]+)',?\s*$/gm)].map((m) => `tag--${m[1]}`)

    expect(tones.length).toBeGreaterThan(0)
    for (const tone of tones) expect(defined).toContain(tone)
  })
})

describe('控件高度统一', () => {
  /*
    各写各的内边距是"看起来没对齐"最常见的原因：输入框 10px 内边距 + 1.5 行高 +
    2px 边框 = 43px，而小按钮只有 27px，并排放就明显矮一截。
    「刷新」按钮就这样比旁边的筛选框矮了一截。

    测试环境没有布局引擎，算不出真实高度，所以断言的是**它们引用同一个令牌** ——
    将来谁把某一处改成写死的值，这条会失败。
  */

  it('令牌已定义', () => {
    expect(tokensCss).toMatch(/--control-height:\s*\d+px/)
  })

  it('输入框与下拉都取这个令牌', () => {
    const inputRule = ruleOf(
      componentsCss,
      /\.field input:not\(\[type='checkbox'\]\):not\(\[type='radio'\]\),[\s\S]*?\}/,
    )
    expect(inputRule).toContain('var(--control-height)')

    const selectSource = readFileSync(resolve(srcDir, 'components/ui/Select.vue'), 'utf-8')
    const triggerRule = ruleOf(selectSource, /\.select__trigger\s*\{[^}]*\}/)
    expect(triggerRule).toContain('var(--control-height)')
  })

  it('与输入框并排的按钮也取这个令牌', () => {
    const controlBtn = ruleOf(componentsCss, /\.btn--control\s*\{[^}]*\}/)
    expect(controlBtn).toContain('var(--control-height)')
  })

  it('筛选行里的查询按钮用的是这一档，而不是小按钮', () => {
    /*
      筛选面板现在是嵌套结构（外层 .filters，里面两行 .filters__row），所以不能
      用非贪婪匹配到第一个 `</div>` —— 那会在第一行结束时就截断。
      这里按"从 .filters 开始到 .pick 之前"取，`.pick` 是列表底部那一行。
    */
    const usersView = readFileSync(resolve(srcDir, 'views/admin/UsersView.vue'), 'utf-8')
    const start = usersView.indexOf('<div class="panel filters">')
    const end = usersView.indexOf('<div class="pick">')
    expect(start).toBeGreaterThan(-1)
    expect(end).toBeGreaterThan(start)

    const filterRow = usersView.slice(start, end)
    expect(filterRow).toContain('btn--control')
    expect(filterRow).not.toContain('btn--small')
  })
})

describe('表单控件不留系统默认外观', () => {
  /*
    暗色界面上，任何一个漏掉样式的控件都会回落到浏览器的浅色默认外观，白得刺眼。

    这类失效**不会报错**：选择器少写一个，那个控件就悄悄变了样。textarea 就这样
    掉过一次 —— 给复选框加 `:not()` 排除时顺手把它从规则里带走了，活动简介框
    当场变成白底。
  */
  const surfaceRule = ruleOf(
    componentsCss,
    /\.field input:not\(\[type='checkbox'\]\):not\(\[type='radio'\]\),[\s\S]*?\}/,
  )

  it('文本输入、下拉、多行文本共用同一条表面样式', () => {
    expect(surfaceRule).toContain(".field input:not([type='checkbox']):not([type='radio'])")
    expect(surfaceRule).toContain('.field select')
    // 这一条最容易被漏掉
    expect(surfaceRule).toContain('.field textarea')
  })

  it('表面样式给出了背景、边框与内边距', () => {
    // 少了任何一条都会露出系统默认外观
    expect(surfaceRule).toContain('background: var(--bg)')
    expect(surfaceRule).toContain('border: 1px solid var(--line)')
    expect(surfaceRule).toContain('padding: 10px 12px')
  })

  it('按钮文字用 flex 居中，而不是靠内边距凑', () => {
    /*
      line-height: 1 时内容盒只有 13px，加内边距 20px 是 33px；而 .btn--control 的
      最小高度是 43px —— 多出来的全落在下方，文字看起来偏高。
    */
    const btnRule = ruleOf(componentsCss, /\.btn\s*\{[^}]*\}/)
    expect(btnRule).toContain('display: inline-flex')
    expect(btnRule).toContain('align-items: center')
  })
})

describe('滚动条', () => {
  /*
    不设样式的话，暗色界面上会出现系统配色的亮条。表格、下拉、对话框都会滚动，
    一处不统一就会在好几处同时刺眼。
  */
  it('给 Firefox 标准属性', () => {
    expect(baseCss).toContain('scrollbar-width')
    expect(baseCss).toContain('scrollbar-color')
  })

  it('给 Chromium / Safari 的伪元素', () => {
    expect(baseCss).toContain('::-webkit-scrollbar')
    expect(baseCss).toContain('::-webkit-scrollbar-thumb')
  })

  it('滑块用令牌取色，不写死颜色', () => {
    const thumbRule = ruleOf(baseCss, /::-webkit-scrollbar-thumb\s*\{[^}]*\}/)
    expect(thumbRule).toContain('var(--line-strong)')
  })

  it('轨道透明，不额外加一条底', () => {
    const trackRule = ruleOf(baseCss, /::-webkit-scrollbar-track,[\s\S]*?\}/)
    expect(trackRule).toContain('transparent')
  })
})

describe('表格滚动容器只做横向', () => {
  /*
    加过一版 `max-height: 70vh`，结果表格内外各一条滚动条：70vh 只算表格自己，
    页面还要装页头、筛选、分页器 —— 于是页面也滚，用户看到两个滚动区域。

    顺带一提，`overflow-x: auto` 会让 `position: sticky` 相对这个盒子生效而不是
    视口，所以"表格内部纵向滚 + 表头吸顶"这个组合本身就是不成立的。
  */
  const scrollRule = ruleOf(componentsCss, /\.table-scroll\s*\{[^}]*\}/)

  it('容器存在且允许横向滚动', () => {
    expect(scrollRule.length).toBeGreaterThan(0)
    expect(scrollRule).toMatch(/overflow(-x)?:\s*auto/)
  })

  it('不限制高度 —— 限制就会出现第二条滚动条', () => {
    expect(scrollRule).not.toContain('max-height')
    expect(scrollRule).not.toContain('height')
  })

  it('固定列宽的表格在容器里保有自己的最小宽度', () => {
    // 否则窄窗口下列宽被压缩，"定宽 + 截断"整套失效
    const minWidthRule = ruleOf(componentsCss, /\.table-scroll\s+\.table--fixed\s*\{[^}]*\}/)
    expect(minWidthRule).toContain('min-width')
  })

  it('没有半途而废的吸顶表头', () => {
    // 横向滚动容器让 sticky 失效，留着只会让人以为它该生效
    expect(componentsCss).not.toContain('.table-scroll .table thead th')
  })
})

describe('外壳的滚动归属', () => {
  /*
    滚动**只发生在内容层**，整页不滚、左侧导航不滚。

    这是布局里最容易悄悄退化的一处：只要把 `.shell` 的 `height: 100%` 换回
    `min-height: 100%`，外壳就跟着内容一起长高，滚动重新回到页面上 —— 左侧导航
    跟着滚走，右侧还多出一条滚动条。**不会有任何报错**，只有肉眼能看出来。
  */
  const shellSource = readFileSync(resolve(srcDir, 'components/layout/AdminShell.vue'), 'utf-8')
  const shellRule = ruleOf(shellSource, /\.shell\s*\{[^}]*\}/)
  const sideRule = ruleOf(shellSource, /\.shell__side\s*\{[^}]*\}/)
  const mainRule = ruleOf(shellSource, /\.shell__main\s*\{[^}]*\}/)
  const scrollRule = ruleOf(shellSource, /\.shell__scroll\s*\{[^}]*\}/)

  it('外壳固定为视口高度并裁掉溢出', () => {
    expect(shellRule).toContain('height: 100%')
    expect(shellRule).toContain('overflow: hidden')
    // min-height 会让外壳跟着内容长高，滚动就回到页面上了
    expect(shellRule).not.toContain('min-height')
  })

  it('**约束了网格行** —— 否则内容是截断的，而且滚不动', () => {
    /*
      这条是踩过的坑：只定义 `grid-template-columns` 时，那一行是隐式的 `auto` 行，
      会跟着内容长高。格子里的滚动层于是也变成内容那么高（比可视区还高），再被外壳
      裁掉 —— 表现是内容被截断**且无法滚动**。

      只写 `height: 100%` 是不够的：外壳的高度确实固定了，但里面那一行没有。
    */
    expect(shellRule).toContain('grid-template-rows')
    expect(shellRule).toMatch(/grid-template-rows:\s*minmax\(0,\s*1fr\)/)
  })

  it('左侧不滚动', () => {
    expect(sideRule).toContain('overflow: hidden')
  })

  it('中间一层只负责裁掉溢出', () => {
    expect(mainRule).toContain('overflow: hidden')
    // grid 子项默认 min-height:auto，会被内容顶高，内层就再也滚不起来
    expect(mainRule).toContain('min-height: 0')
  })

  it('内容层才是滚动的那一层', () => {
    expect(scrollRule).toContain('overflow-y: auto')
    expect(scrollRule).toContain('height: 100%')
  })

  it('模板里确实套了那一层', () => {
    expect(shellSource).toContain('class="shell__scroll"')
    expect(shellSource).toMatch(/shell__scroll[\s\S]*RouterView/)
  })

  it('窄屏退回整页滚动', () => {
    // 上下堆叠时内层再滚会变成两个窄条
    const media = /@media \(max-width: 720px\)\s*\{[\s\S]*?\n\}/.exec(shellSource)?.[0] ?? ''
    expect(media).toContain('overflow: visible')
    expect(media).toContain('height: auto')
    // 两行各自按内容算，1fr 会把它们压平
    expect(media).toContain('grid-template-rows: auto')
  })
})

describe('绝对定位必须有定位祖先', () => {
  /*
    `position: absolute` 的元素，其包含块是最近的定位祖先 —— 没有的话就是初始
    包含块（视口）。关键后果是：**它不受任何祖先 `overflow` 的裁剪**。

    隐藏的输入框（复选框、文件选择）正是这个写法。列表里成百个这样的 1px 元素按
    静态位置一路铺下去，会把整个文档撑得比视口还高 —— 表现是"整页还能滚，滚下去
    是一片空白"，而且完全看不出跟那些看不见的输入框有关。踩过一次。

    所以这里做一条通用检查：**源码里出现 absolute，就必须在同一文件里出现 relative**。
    它比逐个组件去想去查可靠得多。
  */
  it('用到 absolute 的文件里都有 relative', () => {
    const offenders: string[] = []

    for (const raw of collectSources(srcDir)) {
      /*
        **必须先剥掉注释。** 注释里常常提到属性名（"见 `.checkbox` 的
        `position: relative`"），直接对全文 includes 会命中注释而不是声明 ——
        把声明删掉，测试照样通过。这个坑栽过两次了（另一次是 `min-width: 0`），
        变异测试每次都抓了出来。
      */
      const source = raw.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '')

      if (source.includes('position: absolute') && !source.includes('position: relative')) {
        const hint = /\.([a-z][\w-]*(?:__[\w-]+)?)\s*\{[^}]*position:\s*absolute/.exec(source)
        offenders.push(hint?.[1] ?? '(未知选择器)')
      }
    }

    expect(offenders).toEqual([])
  })
})

describe('通知不挡住底下的内容', () => {
  /*
    通知容器铺在右下角一整片。容器若照常接收指针事件，那片区域就会变成死区 ——
    底下明明有按钮，却点不动，而且看不出原因（通知本身可能只有一条、只占一小块）。

    所以：**容器 `pointer-events: none`，条目自己 `auto`**。少了后一半，通知上的
    关闭按钮就点不动了；少了前一半，右下角就废了。
  */
  const source = () =>
    readFileSync(resolve(srcDir, 'components/ui/ToastHost.vue'), 'utf-8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/\/\/[^\n]*/g, '')

  it('容器不吃指针事件', () => {
    const css = source()
    const container = /\.toasts\s*\{[^}]*\}/.exec(css)?.[0] ?? ''

    expect(container).toContain('position: fixed')
    expect(container).toContain('pointer-events: none')
  })

  it('条目自己重新打开指针事件', () => {
    // 少了这一条，通知上的关闭按钮就点不动
    const css = source()
    const item = /\.toast\s*\{[^}]*\}/.exec(css)?.[0] ?? ''

    expect(item).toContain('pointer-events: auto')
  })
})
