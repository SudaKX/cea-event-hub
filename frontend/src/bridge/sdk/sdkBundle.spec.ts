/**
 * SDK **构建产物**的行为验证。
 *
 * 这一条是补上来的：之前只检查了源码里"有没有写 `window.CEA`"，于是漏掉了
 * 打包器把那个全局覆盖掉的情形 —— `build.lib.name = 'CEA'` 会生成
 * `var CEA = <exports>`，在全局作用域把模块内部挂好的 `window.CEA` 冲掉，
 * 活动页拿到的是空对象，`CEA.ready` 为 undefined，`await CEA.ready` 得到
 * undefined，随后 `.loggedIn` 抛错。
 *
 * 教训是：**产物必须在运行时被真的加载一次**，静态 grep 证明不了任何事。
 * 因此这里读取构建好的文件、在 DOM 环境里执行它，然后检查 `window.CEA`。
 *
 * 依赖 `npm run build:sdk` 先跑过（`npm test` 已经串好了）。
 */
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { beforeAll, describe, expect, it, vi } from 'vitest'

const BUNDLE = resolve(process.cwd(), 'public/sdk/v1/cea.js')

describe('SDK 产物', () => {
  beforeAll(() => {
    if (!existsSync(BUNDLE)) {
      throw new Error(
        `找不到 ${BUNDLE}。请先运行 npm run build:sdk（npm test 会自动先跑）。`,
      )
    }
    // 在 DOM 环境里执行产物。vitest 的 happy-dom 环境下全局对象就是 window，
    // 因此产物里的 window.CEA = api 会落到同一个全局上。
    const code = readFileSync(BUNDLE, 'utf-8')
    new Function(code)()
  })

  it('把 API 挂在 window.CEA 上', () => {
    const cea = (globalThis as unknown as { CEA?: unknown }).CEA
    expect(cea).toBeDefined()
    expect(typeof cea).toBe('object')
  })

  it('CEA.ready 是一个 Promise（而不是 undefined）', () => {
    // 这正是出问题的那个字段：空对象时它是 undefined，
    // 于是 `await CEA.ready` 得到 undefined，读 .loggedIn 直接抛错
    const cea = (globalThis as unknown as { CEA: Record<string, unknown> }).CEA
    expect(cea.ready).toBeDefined()
    expect(typeof (cea.ready as Promise<unknown>).then).toBe('function')
  })

  it('暴露了活动页需要的全部方法', () => {
    const cea = (globalThis as unknown as { CEA: Record<string, unknown> }).CEA
    for (const method of [
      'identity',
      'event',
      'me',
      'mySubmissions',
      'submit',
      'toast',
      'navigate',
      'setTitle',
      'resize',
    ]) {
      expect(typeof cea[method], method).toBe('function')
    }
  })

  it('暴露了草稿的三个方法', () => {
    const cea = (globalThis as unknown as { CEA: { draft: Record<string, unknown> } }).CEA
    expect(cea.draft).toBeDefined()
    for (const method of ['save', 'load', 'clear']) {
      expect(typeof cea.draft[method], method).toBe('function')
    }
  })

  it('产物没有创建会覆盖 window.CEA 的全局变量', () => {
    // 这是本次缺陷的直接成因：Rollup 的 lib.name 会生成 `var CEA = ...`
    const code = readFileSync(BUNDLE, 'utf-8')
    expect(code).not.toMatch(/^\s*var\s+CEA\s*=/m)
    expect(code).not.toMatch(/exports\.\w+\s*=/)
  })

  it('产物是经典脚本，不含模块语法', () => {
    // <script type="module"> 受 CORS 限制，而活动页处于不透明源
    const code = readFileSync(BUNDLE, 'utf-8')
    expect(code).not.toMatch(/^\s*import\s/m)
    expect(code).not.toMatch(/^\s*export\s/m)
  })

  it('在收到宿主初始化之前，ready 保持未兑现而不是立刻给错值', async () => {
    const cea = (globalThis as unknown as { CEA: { identity: () => unknown } }).CEA
    // 还没握手，同步读取应当是 null 而不是抛错
    expect(cea.identity()).toBeNull()
  })

  it('收到宿主初始化后，ready 兑现为身份描述符', async () => {
    // 这就是线上出问题的那条路径：活动页 `await CEA.ready` 之后直接读
    // `.loggedIn`。如果 ready 不是 Promise（例如被空对象顶掉），
    // `await undefined` 会得到 undefined，随后读属性就抛 TypeError。
    const identity = {
      loggedIn: true,
      userId: 7,
      displayName: 'Alice',
      role: 'user',
      clientId: 'browser-abc',
      submissionRequiresLogin: false,
    }

    const message = new MessageEvent('message', {
      data: { v: 1, type: 'hub:init', payload: { identity } },
    })
    // SDK 用窗口引用判定发送方；happy-dom 里顶层窗口的 parent 就是自己
    Object.defineProperty(message, 'source', { value: globalThis.window?.parent ?? globalThis })
    globalThis.dispatchEvent(message)

    const cea = (globalThis as unknown as { CEA: { ready: Promise<unknown> } }).CEA
    await expect(cea.ready).resolves.toEqual(identity)
  })

  it('把宿主的进度回推转交给 onProgress', async () => {
    // 这一条也是补上来的：宿主一直在回推 hub:upload-progress，但 SDK 从没处理
    // 过它，只在结束时补了一次 (1, 1) —— 参数看着像进度，实际是假的。
    const cea = (globalThis as unknown as {
      CEA: { submit: (options: unknown) => Promise<unknown> }
    }).CEA

    const sent: Array<{ type?: string; id?: string }> = []
    const spy = vi
      .spyOn(globalThis as unknown as { postMessage: (msg: unknown) => void }, 'postMessage')
      .mockImplementation((msg: unknown) => {
        sent.push(msg as { type?: string; id?: string })
      })

    const progress: Array<[number, number | null]> = []
    const pending = cea.submit({
      payload: { a: 1 },
      onProgress: (loaded: number, total: number | null) => progress.push([loaded, total]),
    })

    // 等 SDK 内部 await ready 之后把 RPC 发出去
    await new Promise((resolve) => setTimeout(resolve, 0))
    const rpc = sent.find((message) => message.type === 'event:rpc')
    expect(rpc, '没有发出 RPC').toBeDefined()

    const source = globalThis.window?.parent ?? globalThis
    const dispatch = (data: unknown) => {
      const event = new MessageEvent('message', { data })
      Object.defineProperty(event, 'source', { value: source })
      globalThis.dispatchEvent(event)
    }

    dispatch({
      v: 1,
      type: 'hub:upload-progress',
      payload: { requestId: rpc!.id, loaded: 30, total: 100 },
    })
    expect(progress).toEqual([[30, 100]])

    dispatch({
      v: 1,
      type: 'hub:upload-progress',
      payload: { requestId: rpc!.id, loaded: 70, total: 100 },
    })
    expect(progress).toEqual([
      [30, 100],
      [70, 100],
    ])

    // 收尾，避免留下悬挂的 promise
    dispatch({ v: 1, type: 'hub:result', id: rpc!.id, payload: { ok: true, data: {} } })
    await pending
    spy.mockRestore()
  })

  it('进度回推的 requestId 不匹配时不影响别的请求', async () => {
    const cea = (globalThis as unknown as {
      CEA: { submit: (options: unknown) => Promise<unknown> }
    }).CEA

    const progress: unknown[] = []
    const spy = vi
      .spyOn(globalThis as unknown as { postMessage: (msg: unknown) => void }, 'postMessage')
      .mockImplementation(() => {})

    const pending = cea.submit({
      payload: { b: 2 },
      onProgress: (loaded: number) => progress.push(loaded),
    })
    await new Promise((resolve) => setTimeout(resolve, 0))

    const source = globalThis.window?.parent ?? globalThis
    const dispatch = (data: unknown) => {
      const event = new MessageEvent('message', { data })
      Object.defineProperty(event, 'source', { value: source })
      globalThis.dispatchEvent(event)
    }

    dispatch({
      v: 1,
      type: 'hub:upload-progress',
      payload: { requestId: 'r-does-not-exist', loaded: 99, total: 100 },
    })
    expect(progress).toEqual([])

    // 收尾
    const message = new MessageEvent('message', {
      data: { v: 1, type: 'hub:result', id: 'r-unknown', payload: { ok: true } },
    })
    Object.defineProperty(message, 'source', { value: source })
    globalThis.dispatchEvent(message)
    spy.mockRestore()

    // 上面那条 result 的 id 对不上，promise 仍然悬挂 —— 用超时兜底释放
    await Promise.race([pending, new Promise((resolve) => setTimeout(resolve, 20))])
  })
})
