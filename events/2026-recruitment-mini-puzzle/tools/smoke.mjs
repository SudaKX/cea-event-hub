/**
 * 浏览器走查（smoke）：在**真浏览器、真沙箱 iframe、真宿主**里把谜题玩一遍。
 *
 *   npm run smoke              # 源码与构建产物各跑一遍
 *   npm run smoke -- src       # 只跑源码
 *   npm run smoke -- dist      # 只跑构建产物（需要先 npm run build）
 *
 * ## 为什么必须有这一条
 *
 * 单测跑的是纯逻辑，构建测试验的是静态产物 —— 两者都看不见"混淆之后的代码在浏览器里
 * 到底跑不跑得起来"。写这一页时被它抓到的问题，全都是单测看不见的：
 *
 *   1. 合并 core 与 app 时少了分号，ASI 把两段 IIFE 连成了一次调用
 *   2. 合并后的脚本插在 SDK **之前**，启动时读不到 window.CEA
 *   3. `boot` 写成具名函数表达式，hub:session 回调里调用它抛 ReferenceError
 *   4. 密钥注进了注释里（锚点原文在注释中重复出现），运行时用的是空密钥
 *
 * 前三条在源码上就能复现，第四条只在产物里出现 —— 所以默认两个目标都跑。
 *
 * ## 前置条件
 *
 * 后端与前端开发服务器都要在跑（见 docs/dev-harness.md）：
 *
 *   cd backend  && ../.venv/Scripts/python.exe -m uvicorn app.main:app --reload
 *   cd frontend && npm run dev
 *
 * 浏览器用本机 Chrome；不在默认位置时设 CHROME_PATH。
 *
 * ## 它刻意**不**做的一件事
 *
 * 不把邮箱真提交上去：那会让后端往 var/app.db 写一条记录。成功路径由"伪造 rate_limited
 * 的错误分支 + 预置 passed 标记"两头夹住（见 step 5 与 step 8），于是既验证了活动页
 * 的全部渲染分支，又不在数据库里留下垃圾。
 */
import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const BROWSER_PORT = 9222
const HOST = 'http://localhost:5173'
const EVENT_ID = process.env.SMOKE_EVENT_ID || 'dev'
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

const TARGETS = {
  src: '/draft/2026-recruitment-mini-puzzle/index.html',
  dist: '/draft/2026-recruitment-mini-puzzle/dist/index.html',
}

// ---------------------------------------------------------------------------
// 极简 CDP 客户端
// ---------------------------------------------------------------------------

class Cdp {
  constructor(ws) {
    this.ws = ws
    this.sequence = 0
    this.pending = new Map()
    this.events = []
    ws.addEventListener('message', (event) => {
      const message = JSON.parse(event.data)
      if (message.id === undefined) {
        this.events.push(message)
        return
      }
      const entry = this.pending.get(message.id)
      if (!entry) return
      this.pending.delete(message.id)
      if (message.error) entry.reject(new Error(JSON.stringify(message.error)))
      else entry.resolve(message.result)
    })
  }

  static async connect() {
    for (let attempt = 0; attempt < 40; attempt++) {
      try {
        const info = await (await fetch(`http://127.0.0.1:${BROWSER_PORT}/json/version`)).json()
        const ws = new WebSocket(info.webSocketDebuggerUrl)
        await new Promise((resolve, reject) => {
          ws.addEventListener('open', resolve)
          ws.addEventListener('error', reject)
        })
        return new Cdp(ws)
      } catch {
        await sleep(500)
      }
    }
    throw new Error('连不上浏览器调试端口，Chrome 没起来？')
  }

  send(method, params = {}, sessionId) {
    const id = ++this.sequence
    const payload = { id, method, params }
    if (sessionId) payload.sessionId = sessionId
    this.ws.send(JSON.stringify(payload))
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      setTimeout(() => {
        if (this.pending.delete(id)) reject(new Error(`CDP 超时：${method}`))
      }, 30000)
    })
  }
}

const listTargets = async () =>
  await (await fetch(`http://127.0.0.1:${BROWSER_PORT}/json/list`)).json()

async function attach(cdp, predicate) {
  const target = (await listTargets()).find(predicate)
  if (!target) throw new Error('找不到目标：' + JSON.stringify((await listTargets()).map((t) => t.url)))
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId: target.id, flatten: true })
  return sessionId
}

async function evaluate(cdp, sessionId, expression) {
  const out = await cdp.send(
    'Runtime.evaluate',
    { expression, returnByValue: true, awaitPromise: true, userGesture: true },
    sessionId,
  )
  if (out.exceptionDetails) {
    throw new Error('页面里抛错：' + JSON.stringify(out.exceptionDetails.exception))
  }
  return out.result.value
}

// ---------------------------------------------------------------------------
// 断言
// ---------------------------------------------------------------------------

const failures = []
let checks = 0

function check(label, condition, detail) {
  checks++
  if (condition) {
    console.log('  ✓ ' + label)
    return
  }
  failures.push(label + (detail === undefined ? '' : ' —— ' + detail))
  console.log('  ✗ ' + label + (detail === undefined ? '' : ' —— ' + detail))
}

// ---------------------------------------------------------------------------
// 页面操作
// ---------------------------------------------------------------------------

const STATE = `JSON.stringify({
  termHidden: document.getElementById('term').hidden,
  deniedHidden: document.getElementById('denied').hidden,
  deniedTitle: document.querySelector('#denied h1').textContent,
  prompt: document.getElementById('prompt').textContent,
  viewerHidden: document.getElementById('viewer').hidden,
})`

const typeIn = (line) => `(async () => {
  const input = document.getElementById('cmd')
  input.focus()
  input.value = ${JSON.stringify(line)}
  input.dispatchEvent(new Event('input', { bubbles: true }))
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
  await new Promise((r) => setTimeout(r, 250))
  return 'ok'
})()`

/** 终端输出的最后 n 行（按行取，避免各行文字连成一串） */
const lastLines = (n) => `[...document.querySelectorAll('#out .line')]
  .slice(-${n}).map((node) => node.textContent).join('\\n')`

const hostRail = (label) => `[...document.querySelectorAll('.rail__btn')]
  .find((b) => b.textContent.includes(${JSON.stringify(label)}))`

function pretendLogin(want) {
  return `(async () => {
    ${hostRail('身份')}.click()
    await new Promise((r) => setTimeout(r, 250))
    const box = [...document.querySelectorAll('input[type=checkbox]')]
      .find((i) => (i.closest('label') || {}).textContent?.includes('冒充已登录'))
    if (box.checked !== ${want}) box.click()
    await new Promise((r) => setTimeout(r, 250))
    return 'pretendLoggedIn=' + box.checked
  })()`
}

const pushSession = `(async () => {
  ${hostRail('动作')}.click()
  await new Promise((r) => setTimeout(r, 250))
  // 勾选复选框不会推送身份，面板只在「动作」页的按钮上调用 pushSession
  ;[...document.querySelectorAll('.actions button')]
    .find((b) => b.textContent.trim() === 'hub:session').click()
  await new Promise((r) => setTimeout(r, 900))
  return 'pushed'
})()`

const setIntercept = (code) => `(async () => {
  ${hostRail('拦截')}.click()
  await new Promise((r) => setTimeout(r, 250))
  const opBox = [...document.querySelectorAll('.ops li input')]
    .find((i) => i.closest('li').textContent.trim() === 'form.submit')
  if (!opBox.checked) opBox.click()
  const autoBox = [...document.querySelectorAll('input[type=checkbox]')]
    .find((i) => (i.closest('label') || {}).textContent?.includes('不必逐条'))
  if (!autoBox.checked) autoBox.click()
  await new Promise((r) => setTimeout(r, 250))
  const selects = [...document.querySelectorAll('.pane__body select')]
  selects[0].value = 'fail'; selects[0].dispatchEvent(new Event('change', { bubbles: true }))
  selects[1].value = ${JSON.stringify(code)}; selects[1].dispatchEvent(new Event('change', { bubbles: true }))
  await new Promise((r) => setTimeout(r, 250))
  return 'intercept=' + ${JSON.stringify(code)}
})()`

// ---------------------------------------------------------------------------
// 一个目标的完整走查
// ---------------------------------------------------------------------------

async function runTarget(cdp, label, src) {
  console.log(`\n=== ${label}：${src} ===`)

  /*
    两个目标共用同一个浏览器 profile 与同一个活动标识，所以上一轮写下的
    `cea.storage:<活动>:passed` 会被下一轮读到 —— 第二个目标就会以"老玩家"身份进入，
    断言全都指向错误的原因（表现为"su 说你已经是以 self 登录的"）。
    在开新页之前先把同源的本地储存清掉。
  */
  const existing = (await listTargets()).find((t) => t.type === 'page' && t.url.startsWith(HOST))
  if (existing) {
    const previous = await attach(cdp, (t) => t.id === existing.id)
    await evaluate(
      cdp,
      previous,
      `(() => {
         for (const key of Object.keys(localStorage)) {
           if (key.startsWith('cea.storage:')) localStorage.removeItem(key)
         }
         return 'cleared'
       })()`,
    )
    await cdp.send('Target.closeTarget', { targetId: existing.id })
    await sleep(500)
  }

  await cdp.send('Target.createTarget', { url: `${HOST}/develop?src=${src}` })
  await sleep(6000)

  let frame = await attach(cdp, (t) => t.type === 'iframe' && t.url.endsWith(src))
  // 用 src 一起匹配宿主页：两个目标的调试台 URL 只差这个查询参数
  const host = await attach(cdp, (t) => t.type === 'page' && t.url.includes(src))
  await cdp.send('Runtime.enable', {}, frame)

  /** 调试台"重新加载 iframe"会换掉整个子框架 —— CDP 的 target 也会换，必须重新挂 */
  async function reattachFrame() {
    for (let attempt = 0; attempt < 20; attempt++) {
      try {
        frame = await attach(cdp, (t) => t.type === 'iframe' && t.url.endsWith(src))
        await cdp.send('Runtime.enable', {}, frame)
        return
      } catch {
        await sleep(500)
      }
    }
    throw new Error('iframe 重载之后挂不上新的 target')
  }

  const state = async () => JSON.parse(await evaluate(cdp, frame, STATE))

  // 1. 未登录：拒绝访问
  let current = await state()
  check('未登录时显示拒绝页、终端不出现', current.deniedHidden === false && current.termHidden === true)

  // 2. 登录态推送后终端启动
  await evaluate(cdp, host, pretendLogin(true))
  await evaluate(cdp, host, pushSession)
  await sleep(1200)
  current = await state()
  check(
    'hub:session 之后终端启动、提示符是 bobby@ellia',
    current.termHidden === false && current.prompt === 'bobby@ellia:~$ ',
    JSON.stringify(current),
  )

  // 3. 命令走查
  await evaluate(cdp, frame, typeIn('help'))
  let text = await evaluate(cdp, frame, lastLines(9))
  check(
    'help 列出七条命令',
    ['ls', 'cd', 'cat', 'echo', 'su', 'download', 'help'].every((c) => text.includes(c)),
  )

  await evaluate(cdp, frame, typeIn('ls'))
  text = await evaluate(cdp, frame, lastLines(1))
  check('ls 只列出 documents 与 pictures', text.trim() === 'documents/  pictures/', JSON.stringify(text))

  await evaluate(cdp, frame, typeIn('ls documents'))
  text = await evaluate(cdp, frame, lastLines(1))
  check('ls documents 列出两个文件', text.includes('password') && text.includes('文本文档.txt'))

  // cd：切进去、用相对路径读、退回来、越界被拒
  await evaluate(cdp, frame, typeIn('cd documents'))
  let moved = JSON.parse(await evaluate(cdp, frame, STATE))
  check('cd documents 之后提示符变成 ~/documents', moved.prompt === 'bobby@ellia:~/documents$ ', moved.prompt)

  await evaluate(cdp, frame, typeIn('ls'))
  text = await evaluate(cdp, frame, lastLines(1))
  check('cd 之后 ls 列的是当前目录', text.trim() === 'password  文本文档.txt', JSON.stringify(text))

  await evaluate(cdp, frame, typeIn('cat password'))
  text = await evaluate(cdp, frame, lastLines(2))
  check('cd 之后可以用相对路径 cat', /username: self/.test(text), text)

  await evaluate(cdp, frame, typeIn('cd ..'))
  moved = JSON.parse(await evaluate(cdp, frame, STATE))
  check('cd .. 回到用户目录', moved.prompt === 'bobby@ellia:~$ ', moved.prompt)

  await evaluate(cdp, frame, typeIn('cd ..'))
  text = await evaluate(cdp, frame, lastLines(1))
  check('站在用户目录上再 cd .. 被拒绝', text.includes('Permission denied'), text)

  await evaluate(cdp, frame, typeIn('cat documents/password'))
  text = await evaluate(cdp, frame, lastLines(2))
  check('cat password 给出账号与口令', /username: self/.test(text) && /password: [0-9a-f]{16}/.test(text))

  // bobby 的 pictures 是空的，那张图在 self 那边
  await evaluate(cdp, frame, typeIn('ls pictures'))
  text = await evaluate(cdp, frame, lastLines(1))
  // 空目录什么都不印：于是最后一行就是刚敲的那条命令行本身
  check('bobby 的 pictures 是空的', text.trim() === 'bobby@ellia:~$ ls pictures', JSON.stringify(text))

  await evaluate(cdp, frame, typeIn('cat pictures/illusion.png'))
  text = await evaluate(cdp, frame, lastLines(1))
  check('bobby 看不到那张图', text.includes('No such file or directory'), text)

  await evaluate(cdp, frame, typeIn('cat /etc/passwd'))
  text = await evaluate(cdp, frame, lastLines(1))
  check('绝对路径被拒绝', text.includes('Permission denied'), text)

  await evaluate(cdp, frame, typeIn('cat ../documents/password'))
  text = await evaluate(cdp, frame, lastLines(1))
  check('.. 越界被拒绝', text.includes('Permission denied'), text)

  await evaluate(cdp, frame, typeIn('mkdir /tmp'))
  text = await evaluate(cdp, frame, lastLines(1))
  check('未提供的命令报 command not found', text.includes('command not found'), text)

  // 4. su：先错后对（download 与看图要等切到 self 之后）
  await evaluate(cdp, frame, typeIn('su self'))
  let current2 = JSON.parse(await evaluate(cdp, frame, STATE))
  check('su self 之后提示符变成 Password', current2.prompt === 'Password: ')

  await evaluate(cdp, frame, typeIn('wrong-password'))
  text = await evaluate(cdp, frame, lastLines(1))
  check('口令错误报 Authentication failure', text.includes('Authentication failure'), text)

  // 口令从终端输出里按行读 —— 与玩家的做法一致，不在脚本里硬编码
  const password = await evaluate(
    cdp,
    frame,
    `(() => {
       const line = [...document.querySelectorAll('#out .line')]
         .map((n) => n.textContent).find((t) => t.startsWith('password: '))
       return line ? line.slice('password: '.length).trim() : null
     })()`,
  )
  check('能从终端输出里读到口令', typeof password === 'string' && password.length === 16)

  await evaluate(cdp, frame, typeIn('su self'))
  await evaluate(cdp, frame, typeIn(password))
  current2 = JSON.parse(await evaluate(cdp, frame, STATE))
  text = await evaluate(cdp, frame, lastLines(4))
  check('口令正确后切到 self 并进入邮箱阶段', current2.prompt === 'email> ', current2.prompt)
  check(
    '欢迎行按约定文案给出',
    text.includes('恭喜通过 2026 招新的迷你解谜，填写邮箱以便后续联系。'),
    text,
  )

  // 5. 邮箱提示只接邮箱：这时候敲命令会被当成邮箱，而不是执行
  await evaluate(cdp, frame, typeIn('ls pictures'))
  text = await evaluate(cdp, frame, lastLines(1))
  check(
    '邮箱阶段敲命令被当成邮箱（不当命令执行）',
    text.includes('邮箱格式看起来不对') && !text.includes('illusion.png'),
    text,
  )
  current2 = JSON.parse(await evaluate(cdp, frame, STATE))
  check('被当成邮箱之后仍停在邮箱阶段', current2.prompt === 'email> ', current2.prompt)

  // self 家目录里的东西要等提交之后才看得到 —— 那一段在最后的"老玩家"步骤里验
  // （真提交会往数据库写记录，所以走查用预置 passed 标记的方式覆盖它）

  // 6. 提交：伪造 rate_limited，验证可重试分支（不发往后端，因此不写数据库）
  await evaluate(cdp, host, setIntercept('rate_limited'))
  await evaluate(cdp, frame, typeIn('someone@example.com'))
  await sleep(1200)
  current = await state()
  text = await evaluate(cdp, frame, lastLines(2))
  check('限流提示出现', text.includes('提交得有点频繁'), text)
  check('仍在邮箱阶段，可以重试', current.prompt === 'email> ')

  const markers = await evaluate(
    cdp,
    host,
    `JSON.stringify(Object.keys(localStorage).filter((k) => k.startsWith('cea.storage:')))`,
  )
  check('失败时没有写下 passed 标记', !markers.includes('passed'), markers)

  // 7. 会话变化：登出退回拒绝页，重新登录能自己回来
  await evaluate(cdp, host, pretendLogin(false))
  await evaluate(cdp, host, pushSession)
  await sleep(800)
  current = await state()
  check('登出后立刻退回拒绝页', current.deniedHidden === false && current.termHidden === true)

  await evaluate(cdp, host, pretendLogin(true))
  await evaluate(cdp, host, pushSession)
  await sleep(1200)
  current = await state()
  check('重新登录后终端再次启动', current.termHidden === false && current.prompt === 'bobby@ellia:~$ ')

  // 8. 老玩家：预置 passed 标记后重进，应当直接是 self，且不再问邮箱
  await evaluate(
    cdp,
    host,
    `localStorage.setItem('cea.storage:${EVENT_ID}:passed', JSON.stringify({ email: 'x@example.com', submissionId: 1 })); 'seeded'`,
  )
  await evaluate(
    cdp,
    host,
    `(async () => {
       ${hostRail('状态')}.click()
       await new Promise((r) => setTimeout(r, 250))
       ;[...document.querySelectorAll('button')].find((b) => b.textContent.includes('重新加载 iframe')).click()
       await new Promise((r) => setTimeout(r, 2500))
       return 'reloaded'
     })()`,
  )
  await sleep(1000)
  await reattachFrame()
  current = await state()
  const greeting = await evaluate(cdp, frame, lastLines(4))
  check('已通过的玩家直接是 self@ellia', current.prompt === 'self@ellia:~$ ', current.prompt)
  check('已通过的玩家不再被要求填邮箱', greeting.includes('欢迎回来'), greeting)

  /*
    这里补上"提交之后才看得到的东西"：self 的家目录与那张图。

    放在这一步是因为**真提交会往数据库写记录**（走查刻意不写），而预置 passed 标记
    得到的正是"交完邮箱、以 self 回到 shell"那个状态 —— 与玩家走完流程后看到的一样。
  */
  await evaluate(cdp, frame, typeIn('ls documents'))
  text = await evaluate(cdp, frame, lastLines(1))
  check('self 的 documents 里是 data', text.trim() === 'data', JSON.stringify(text))

  await evaluate(cdp, frame, typeIn('cat documents/data'))
  text = await evaluate(cdp, frame, lastLines(1))
  check('self 的 data 有内容', /不再有意义了。$/.test(text.trim()), text)

  await evaluate(cdp, frame, typeIn('ls pictures'))
  text = await evaluate(cdp, frame, lastLines(1))
  check('self 的 pictures 里有那张图', text.trim() === 'illusion.png', JSON.stringify(text))

  // download：图要真的显示出来，而且按容器缩放、不出滚动条
  await evaluate(cdp, frame, typeIn('download pictures/illusion.png'))
  const viewer = JSON.parse(
    await evaluate(
      cdp,
      frame,
      `JSON.stringify({
         hidden: document.getElementById('viewer').hidden,
         size: document.getElementById('viewer-size').textContent,
         imageClass: document.getElementById('viewer-body').classList.contains('viewer__body--image'),
         fits: (() => {
           const body = document.getElementById('viewer-body')
           const img = document.querySelector('#viewer-body img')
           if (!img) return null
           const b = body.getBoundingClientRect()
           const i = img.getBoundingClientRect()
           return {
             noScroll: body.scrollHeight <= body.clientHeight + 1 && body.scrollWidth <= body.clientWidth + 1,
             insideBox: i.height <= b.height + 1 && i.width <= b.width + 1,
           }
         })(),
         img: (() => {
           const img = document.querySelector('#viewer-body img')
           return img ? { complete: img.complete, w: img.naturalWidth, h: img.naturalHeight } : null
         })(),
       })`,
    ),
  )
  check(
    'download 打开查看器并真的加载了图片',
    viewer.hidden === false && viewer.img && viewer.img.complete && viewer.img.w > 0,
    JSON.stringify(viewer),
  )
  check('查看器显示真实字节数', viewer.size.includes('1827694'), viewer.size)
  check(
    '图片按容器缩放，查看器里不出滚动条',
    viewer.imageClass === true && viewer.fits && viewer.fits.noScroll && viewer.fits.insideBox,
    JSON.stringify(viewer.fits),
  )

  await evaluate(
    cdp,
    frame,
    `(async () => {
       document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
       await new Promise((r) => setTimeout(r, 200))
       return 'closed'
     })()`,
  )
  current = await state()
  check('Esc 关闭查看器', current.viewerHidden === true)

  // 滚动条：终端用的是自定义的那条（细），不是系统默认的宽条
  const scrollbar = JSON.parse(
    await evaluate(
      cdp,
      frame,
      `(() => {
         const body = document.getElementById('screen')
         return JSON.stringify({
           gutter: body.offsetWidth - body.clientWidth,
           width: getComputedStyle(body).scrollbarWidth,
           color: getComputedStyle(document.documentElement).scrollbarColor,
         })
       })()`,
    ),
  )
  check(
    '终端滚动条是细的（自定义样式生效）',
    scrollbar.gutter <= 12 && scrollbar.gutter >= 0,
    JSON.stringify(scrollbar),
  )

  // 页面里不该有未捕获异常
  const exceptions = cdp.events
    .filter((e) => e.method === 'Runtime.exceptionThrown')
    .map((e) => e.params.exceptionDetails.exception?.description || e.params.exceptionDetails.text)
  check('页面没有未捕获异常', exceptions.length === 0, exceptions.join(' / '))
}

// ---------------------------------------------------------------------------
// 入口
// ---------------------------------------------------------------------------

function findChrome() {
  const candidates = [
    process.env.CHROME_PATH,
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
  ].filter(Boolean)
  return candidates.find((path) => existsSync(path))
}

async function ensureServers() {
  try {
    const response = await fetch(`${HOST}/develop`)
    if (!response.ok) throw new Error(String(response.status))
  } catch (error) {
    throw new Error(
      `开发服务器没在跑（${HOST}/develop）。先按 docs/dev-harness.md 起后端与前端，再跑这条。${error.message}`,
    )
  }
}

const requested = (process.argv[2] || 'both').toLowerCase()
const targets = requested === 'both' ? ['src', 'dist'] : [requested]
for (const target of targets) {
  if (!(target in TARGETS)) throw new Error(`未知目标：${target}（可用 src / dist / both）`)
}
if (targets.includes('dist') && !existsSync(join(import.meta.dirname, '..', 'dist', 'index.html'))) {
  throw new Error('dist 不存在，先跑 npm run build')
}

await ensureServers()

const chromePath = findChrome()
if (!chromePath) throw new Error('找不到 Chrome，可用 CHROME_PATH 指定')

const profile = mkdtempSync(join(tmpdir(), 'cea-smoke-'))
const chrome = spawn(
  chromePath,
  [
    '--headless=new',
    `--remote-debugging-port=${BROWSER_PORT}`,
    `--user-data-dir=${profile}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-extensions',
    '--window-size=1280,900',
    'about:blank',
  ],
  { stdio: 'ignore' },
)

let exitCode = 0
try {
  const cdp = await Cdp.connect()
  for (const target of targets) {
    await runTarget(cdp, target === 'src' ? '源码' : '构建产物', TARGETS[target])
  }
} catch (error) {
  failures.push('走查中断：' + error.message)
  console.error('\n走查中断：', error.message)
} finally {
  chrome.kill()
  await sleep(500)
  rmSync(profile, { recursive: true, force: true })
}

console.log(`\n合计 ${checks} 项断言，失败 ${failures.length} 项`)
if (failures.length) {
  console.log('失败项：')
  for (const failure of failures) console.log('  - ' + failure)
  exitCode = 1
}
process.exit(exitCode)
