/**
 * 解谜核心的单测。
 *
 * 跑的是 index.html 里 `<script id="puzzle-core">` 那一块 —— 也就是线上真正执行的同一份
 * 代码，而不是它的副本。这样"测试通过"才等于"页面上的规则是对的"。
 *
 *   npm test        （在 events/2026-recruitment-mini-puzzle 下）
 */
import assert from 'node:assert/strict'
import { describe, test } from 'node:test'

import { loadCore, loadFs, readSource } from '../tools/source.mjs'

const core = loadCore()
const FS = loadFs()

/** 每次都给一份干净的文件系统，避免用例之间互相影响 */
function makeSession(options) {
  return core.createSession(structuredClone(FS), options)
}

/** 行 -> 纯文本，便于断言 */
function textOf(outcome) {
  return outcome.lines.map((line) =>
    line.parts ? line.parts.map((part) => part.text).join('') : line.text,
  )
}

/** 逐条敲命令，返回全部输出文本 */
function type(session, ...inputs) {
  const all = []
  for (const input of inputs) all.push(...textOf(session.run(input)))
  return all
}

function passwordOf(session) {
  return session.data().credential.password
}

function passThrough(session) {
  // 用文件里的口令走完 su：测试不硬编码口令，改文件时测试跟着走
  session.run('su self')
  return session.run(passwordOf(session))
}

/**
 * 走完 su **并把邮箱交上去**，落到 self 的 shell。
 *
 * 邮箱提示只接邮箱（命令那时不可用），所以"以 self 身份探索"必须先把这一步走完 ——
 * 这也正是玩家实际会经历的次序。
 */
function passAndSubmit(session) {
  passThrough(session)
  session.run('someone@example.com')
  session.resolveSubmit({ ok: true, id: 1 })
  return session
}

describe('路径限制在用户目录内', () => {
  test('绝对路径被拒绝，而不是"找不到"', () => {
    const out = type(makeSession(), 'cat /etc/passwd')
    assert.deepEqual(out, ['cat: /etc/passwd: Permission denied'])
  })

  test('站在用户目录上时 .. 被拒绝 —— 那是这个世界之外', () => {
    const session = makeSession()
    assert.deepEqual(type(session, 'cat ../documents/password'), [
      'cat: ../documents/password: Permission denied',
    ])
    assert.deepEqual(type(session, 'cd ..'), ['cd: ..: Permission denied'])
    assert.deepEqual(type(session, 'cd ../../..'), ['cd: ../../..: Permission denied'])
  })

  test('子目录里 .. 是好用的 —— 越界判定是"边走边夹"，不是一律拒绝', () => {
    const session = makeSession()
    session.run('cd documents')
    // documents/../documents/password：绕了一圈，仍在用户目录之内
    assert.deepEqual(
      textOf(session.run('cat ../documents/password')),
      textOf(makeSession().run('cat documents/password')),
    )

    session.run('cd ../pictures')
    assert.equal(session.state().cwdDisplay, '~/pictures')
    // bobby 的 pictures 是空的，所以这里只断言"已经站在那儿了"
    assert.deepEqual(textOf(session.run('ls')), [])
  })

  test('~ 与 . 都指向用户目录', () => {
    const session = makeSession()
    const home = textOf(session.run('ls'))[0]
    assert.equal(textOf(session.run('ls ~'))[0], home)
    assert.equal(textOf(session.run('ls .'))[0], home)
    assert.deepEqual(textOf(session.run('ls ~/documents')), textOf(session.run('ls documents')))
  })
})

describe('cd', () => {
  test('切换目录后提示符跟着变', () => {
    const session = makeSession()
    session.run('cd documents')
    assert.deepEqual(session.prompt().parts.map((p) => p.text), ['bobby@ellia', ':', '~/documents', '$ '])
    assert.equal(session.state().cwdDisplay, '~/documents')
    assert.deepEqual(session.state().cwd, ['documents'])
  })

  test('不带参数回用户目录', () => {
    const session = makeSession()
    session.run('cd documents')
    session.run('cd')
    assert.equal(session.state().cwdDisplay, '~')
    assert.deepEqual(session.state().cwd, [])
  })

  test('ls 与 cat 都按当前目录解析', () => {
    const session = makeSession()
    session.run('cd documents')
    assert.deepEqual(textOf(session.run('ls')), ['password  文本文档.txt'])
    assert.match(textOf(session.run('cat password'))[0], /^username: self$/)
    // 站在 documents 里，pictures 就不在当前目录下了
    assert.deepEqual(textOf(session.run('ls pictures')), [
      "ls: cannot access 'pictures': No such file or directory",
    ])
  })

  test('cd .. 回到上一层', () => {
    const session = makeSession()
    session.run('cd documents')
    session.run('cd ..')
    assert.equal(session.state().cwdDisplay, '~')
    assert.deepEqual(textOf(session.run('ls')), ['documents/  pictures/'])
  })

  test('~ 与 ~/子目录 都从用户目录起算', () => {
    const session = makeSession()
    session.run('cd documents')
    session.run('cd ~')
    assert.equal(session.state().cwdDisplay, '~')
    session.run('cd ~/pictures')
    assert.equal(session.state().cwdDisplay, '~/pictures')
  })

  test('不存在的目录、文件、越界分别报错', () => {
    const session = makeSession()
    assert.deepEqual(textOf(session.run('cd nope')), ['cd: nope: No such file or directory'])
    assert.deepEqual(textOf(session.run('cd documents/password')), [
      'cd: documents/password: Not a directory',
    ])
    assert.deepEqual(textOf(session.run('cd /etc')), ['cd: /etc: Permission denied'])
    assert.equal(session.state().cwdDisplay, '~')
  })

  test('切到 self 之后当前目录回到 ~（新会话从家目录开始）', () => {
    const session = makeSession()
    session.run('cd documents')
    passThrough(session)
    assert.equal(session.state().cwdDisplay, '~')
  })
})

describe('ls', () => {
  test('用户目录只有 documents 与 pictures', () => {
    const out = textOf(makeSession().run('ls'))
    assert.equal(out.length, 1)
    assert.equal(out[0], 'documents/  pictures/')
  })

  test('列出 documents 下的两个文件', () => {
    assert.deepEqual(textOf(makeSession().run('ls documents')), ['password  文本文档.txt'])
  })

  test('对文件执行 ls 会原样回显参数（与真 ls 一致）', () => {
    assert.deepEqual(textOf(makeSession().run('ls documents/password')), ['documents/password'])
  })

  test('不存在的路径报 cannot access', () => {
    assert.deepEqual(textOf(makeSession().run('ls nope')), [
      "ls: cannot access 'nope': No such file or directory",
    ])
  })

  test('目录项带 dir 语气、文件带 file 语气', () => {
    const tones = makeSession().run('ls').lines[0].parts.map((part) => part.tone)
    assert.deepEqual(tones, ['dir', 'plain', 'dir'])
  })

  test('bobby 的 pictures 是空的 —— 空目录什么都不印（与真 ls 一致）', () => {
    const outcome = makeSession().run('ls pictures')
    assert.deepEqual(textOf(outcome), [])
    assert.deepEqual(outcome.lines, [])
  })
})

describe('每个用户一份家目录', () => {
  test('切到 self 之后 ls 看到的是 self 的东西', () => {
    const session = makeSession()
    passAndSubmit(session)
    assert.deepEqual(textOf(session.run('ls')), ['documents/  pictures/'])
    assert.deepEqual(textOf(session.run('ls documents')), ['data'])
    assert.deepEqual(textOf(session.run('ls pictures')), ['illusion.png'])
  })

  test('那张图只在 self 那边 —— bobby 看不到也下不了', () => {
    const bobby = makeSession()
    assert.deepEqual(textOf(bobby.run('cat pictures/illusion.png')), [
      'cat: pictures/illusion.png: No such file or directory',
    ])
    assert.deepEqual(textOf(bobby.run('download pictures/illusion.png')), [
      'download: pictures/illusion.png: No such file or directory',
    ])
  })

  test('self 的 documents/data 内容与真实文件一致', () => {
    const session = makeSession()
    passAndSubmit(session)
    const out = textOf(session.run('cat documents/data'))
    assert.equal(out.length, 1)
    assert.match(out[0], /^意外？抛弃？/)
    assert.match(out[0], /不再有意义了。$/)
  })

  test('口令文件是 bobby 的 —— 切到 self 之后那边没有它', () => {
    const session = makeSession()
    passAndSubmit(session)
    assert.deepEqual(textOf(session.run('cat documents/password')), [
      'cat: documents/password: No such file or directory',
    ])
  })

  test('切用户时当前目录跟着回到 ~', () => {
    const session = makeSession()
    session.run('cd documents')
    passThrough(session)
    assert.equal(session.state().cwdDisplay, '~')
  })
})

describe('cat', () => {
  test('password 文件给出用户名与口令', () => {
    const out = textOf(makeSession().run('cat documents/password'))
    assert.equal(out[0], 'username: self')
    assert.match(out[1], /^password: [0-9a-f]{16}$/)
  })

  test('文本文档.txt 的内容与真实文件一致', () => {
    assert.deepEqual(textOf(makeSession().run('cat documents/文本文档.txt')), [
      '对不起，都是我的错。',
      '',
      '等我。',
    ])
  })

  test('目录报 Is a directory', () => {
    assert.deepEqual(textOf(makeSession().run('cat documents')), ['cat: documents: Is a directory'])
  })

  test('不存在的文件报 No such file or directory', () => {
    assert.deepEqual(textOf(makeSession().run('cat pictures/nope.png')), [
      'cat: pictures/nope.png: No such file or directory',
    ])
  })

  test('二进制文件只给一行提示，不倒乱码（self 那边才有那张图）', () => {
    const session = makeSession()
    passAndSubmit(session)
    const out = textOf(session.run('cat pictures/illusion.png'))
    assert.equal(out.length, 1)
    assert.match(out[0], /二进制文件（1827694 字节）/)
    assert.match(out[0], /download/)
  })

  test('缺参数时报错而不是静默', () => {
    assert.deepEqual(textOf(makeSession().run('cat')), ['cat: 缺少文件参数'])
  })

  test('~ 前缀可用', () => {
    assert.deepEqual(
      textOf(makeSession().run('cat ~/documents/password')),
      textOf(makeSession().run('cat documents/password')),
    )
  })
})

describe('echo / help / 未知命令', () => {
  test('echo 原样回显，包括空参', () => {
    const session = makeSession()
    assert.deepEqual(textOf(session.run('echo hello world')), ['hello world'])
    assert.deepEqual(textOf(session.run('echo')), [''])
  })

  test('help 列出全部七条命令', () => {
    const out = type(makeSession(), 'help').join('\n')
    for (const command of ['ls', 'cd', 'cat', 'echo', 'su', 'download', 'help']) {
      assert.ok(out.includes(command), `help 里应当出现 ${command}`)
    }
  })

  test('未提供的命令报 command not found', () => {
    assert.deepEqual(textOf(makeSession().run('mkdir /tmp')), ['mkdir: command not found'])
    assert.deepEqual(textOf(makeSession().run('pwd')), ['pwd: command not found'])
  })
})

describe('su：从 bobby 切到 self', () => {
  test('不带参数时报错', () => {
    assert.deepEqual(textOf(makeSession().run('su')), ['su: 需要指定用户名'])
  })

  test('不存在的用户', () => {
    assert.deepEqual(textOf(makeSession().run('su root')), ['su: user root does not exist'])
  })

  test('进入口令输入：提示符变成 Password 且输入掩码', () => {
    const session = makeSession()
    session.run('su self')
    const prompt = session.prompt()
    assert.equal(prompt.mode, 'password')
    assert.equal(prompt.masked, true)
    assert.equal(prompt.parts.map((part) => part.text).join(''), 'Password: ')
  })

  test('口令错误：报 Authentication failure，仍是 bobby', () => {
    const session = makeSession()
    session.run('su self')
    const out = textOf(session.run('wrong-password'))
    assert.deepEqual(out, ['su: Authentication failure'])
    assert.equal(session.state().user, 'bobby')
    assert.equal(session.state().mode, 'shell')
    assert.equal(session.state().passed, false)
  })

  test('口令正确：切到 self，并提示填邮箱', () => {
    const session = makeSession()
    const out = type(session, 'su self', passwordOf(session))
    assert.equal(session.state().user, 'self')
    assert.equal(session.state().mode, 'email')
    assert.deepEqual(out, [
      '已切换为 self@ellia',
      '恭喜通过 2026 招新的迷你解谜，填写邮箱以便后续联系。',
    ])
  })

  test('已经是 self 时再 su self 会被拒', () => {
    // 只有"再次进入"的玩家会以 self 停在 shell 上（刚 su 成功的人停在邮箱阶段）
    const session = makeSession({ startUser: 'self', passed: true })
    assert.deepEqual(textOf(session.run('su self')), ['su: 你已经是以 self 登录的'])
  })

  test('口令比较是精确匹配，大小写与空白都算错', () => {
    const session = makeSession()
    const password = passwordOf(session)
    session.run('su self')
    assert.deepEqual(textOf(session.run(' ' + password)), ['su: Authentication failure'])
    session.run('su self')
    assert.deepEqual(textOf(session.run(password.toUpperCase())), ['su: Authentication failure'])
  })
})

describe('邮箱阶段', () => {
  function atEmailPhase() {
    const session = makeSession()
    passThrough(session)
    return session
  }

  test('格式不对就重问，且不发提交', () => {
    const session = atEmailPhase()
    const outcome = session.run('not-an-email')
    assert.deepEqual(textOf(outcome), ['邮箱格式看起来不对，请重新输入。'])
    assert.deepEqual(outcome.effects, [])
    assert.equal(session.state().mode, 'email')
  })

  test('邮箱提示只接邮箱 —— 这时候敲命令不算命令', () => {
    const session = makeSession()
    session.run('su self')
    const switched = textOf(session.run(passwordOf(session)))
    assert.deepEqual(switched, [
      '已切换为 self@ellia',
      '恭喜通过 2026 招新的迷你解谜，填写邮箱以便后续联系。',
    ])

    for (const line of ['ls pictures', 'cat documents/data', 'download x.png']) {
      assert.deepEqual(
        textOf(session.run(line)),
        ['邮箱格式看起来不对，请重新输入。'],
        `邮箱阶段不该把 ${line} 当命令`,
      )
    }
    assert.equal(session.state().mode, 'email')
    // 也不该进历史 —— 它压根没被当成命令
    assert.deepEqual(session.state().history, ['su self'])
  })

  test('提交成功之后回到 self 的 shell，这时才探索 self 的家目录', () => {
    const session = atEmailPhase()
    session.run('someone@example.com')
    const outcome = session.resolveSubmit({ ok: true, id: 7 })
    assert.equal(session.state().mode, 'shell')
    assert.equal(session.state().user, 'self')

    // 从这里开始命令才恢复
    assert.deepEqual(textOf(session.run('ls documents')), ['data'])
    assert.deepEqual(textOf(session.run('ls pictures')), ['illusion.png'])
    const viewer = session.run('download pictures/illusion.png')
    assert.equal(viewer.effects[0].type, 'viewer')
    assert.match(textOf(outcome).join('\n'), /感谢参与/)
  })

  test('合法邮箱产生一次提交副作用', () => {
    const session = atEmailPhase()
    const outcome = session.run('someone@example.com')
    assert.deepEqual(textOf(outcome), ['提交中…'])
    assert.equal(outcome.effects.length, 1)
    assert.equal(outcome.effects[0].type, 'submit-email')
    assert.equal(outcome.effects[0].email, 'someone@example.com')
    assert.equal(session.state().mode, 'submitting')
  })

  test('提交成功：记为已通过、给出编号、回到 shell', () => {
    const session = atEmailPhase()
    session.run('someone@example.com')
    const outcome = session.resolveSubmit({ ok: true, id: 123 })
    assert.equal(session.state().passed, true)
    assert.equal(session.state().submittedId, 123)
    assert.equal(session.state().mode, 'shell')
    assert.match(textOf(outcome).join('\n'), /#123/)
    assert.equal(session.prompt().parts.map((p) => p.text).join(''), 'self@ellia:~$ ')
  })

  test('rate_limited 可重试：留在邮箱阶段', () => {
    const session = atEmailPhase()
    session.run('someone@example.com')
    session.resolveSubmit({ ok: false, code: 'rate_limited' })
    assert.equal(session.state().mode, 'email')
    assert.equal(session.state().passed, false)
    // 再输一次仍应产生提交
    assert.equal(session.run('someone@example.com').effects.length, 1)
  })

  test('login_required 切到拒绝页', () => {
    const session = atEmailPhase()
    session.run('someone@example.com')
    const outcome = session.resolveSubmit({ ok: false, code: 'login_required' })
    assert.equal(session.state().mode, 'halted')
    assert.deepEqual(
      outcome.effects.map((e) => e.type),
      ['refuse'],
    )
  })

  test('event_closed 与 quota_exhausted 都是终态', () => {
    for (const code of ['event_closed', 'quota_exhausted']) {
      const session = atEmailPhase()
      session.run('someone@example.com')
      const outcome = session.resolveSubmit({ ok: false, code })
      assert.equal(session.state().mode, 'halted', code)
      assert.deepEqual(outcome.effects.map((e) => e.type), ['halt'], code)
    }
  })

  test('submitter_quota_exhausted 当作"你已经交过了"', () => {
    const session = atEmailPhase()
    session.run('someone@example.com')
    session.resolveSubmit({ ok: false, code: 'submitter_quota_exhausted' })
    assert.equal(session.state().passed, true)
    assert.equal(session.state().mode, 'shell')
  })
})

describe('已通过的玩家再次进入', () => {
  function returning() {
    return makeSession({ startUser: 'self', passed: true, submissionId: 42 })
  }

  test('直接就是 self@ellia，不再要求邮箱', () => {
    const session = returning()
    assert.deepEqual(session.prompt().parts.map((p) => p.text), ['self@ellia', ':', '~', '$ '])
    assert.equal(session.state().passed, true)
    assert.match(textOf(session.start())[0], /欢迎回来/)
  })

  test('输入邮箱不会被当成提交，只会是未知命令', () => {
    const session = returning()
    const outcome = session.run('someone@example.com')
    assert.deepEqual(outcome.effects, [])
    assert.deepEqual(textOf(outcome), ['someone@example.com: command not found'])
  })

  test('探索类命令照常可用（看到的是 self 的家目录）', () => {
    const session = returning()
    assert.deepEqual(textOf(session.run('ls documents')), ['data'])
    assert.deepEqual(textOf(session.run('ls pictures')), ['illusion.png'])
  })
})

describe('download', () => {
  test('图片：给出查看器副作用与真实字节数（要切到 self 才有）', () => {
    const session = makeSession()
    passAndSubmit(session)
    const outcome = session.run('download pictures/illusion.png')
    assert.deepEqual(textOf(outcome), [
      'downloaded pictures/illusion.png (1827694 bytes)',
      '已在查看器中打开；要另存用里面的「在新标签页打开」。',
    ])
    assert.equal(outcome.effects.length, 1)
    const file = outcome.effects[0].file
    assert.equal(outcome.effects[0].type, 'viewer')
    assert.equal(file.binary, true)
    assert.equal(file.size, 1827694)
    // 相对**页面**的地址：虚拟家目录是两层的，真实资源就在页面旁边
    assert.equal(file.url, './pictures/illusion.png')
    assert.equal(file.content, null)
  })

  test('图片能在子目录里用相对路径下载', () => {
    const session = makeSession()
    passAndSubmit(session)
    session.run('cd pictures')
    const outcome = session.run('download illusion.png')
    assert.equal(outcome.effects[0].file.url, './pictures/illusion.png')
  })

  test('文本文件也能下载，内容随副作用一起给', () => {
    const outcome = makeSession().run('download documents/password')
    const file = outcome.effects[0].file
    assert.equal(file.binary, false)
    assert.match(file.content, /^username: self\r?\n/)
  })

  test('目录、缺失、越界分别报错', () => {
    const session = makeSession()
    assert.deepEqual(textOf(session.run('download documents')), [
      'download: documents: Is a directory',
    ])
    assert.deepEqual(textOf(session.run('download nope.png')), [
      'download: nope.png: No such file or directory',
    ])
    assert.deepEqual(textOf(session.run('download /etc/shadow')), [
      'download: /etc/shadow: Permission denied',
    ])
    assert.deepEqual(textOf(session.run('download')), ['download: 缺少文件参数'])
  })
})

describe('Ctrl+C 打断', () => {
  test('口令提示被打断后回到 shell，用户不变', () => {
    const session = makeSession()
    session.run('su self')
    session.cancel()
    assert.equal(session.state().mode, 'shell')
    assert.equal(session.state().user, 'bobby')
    // 打断之后敲命令应当按命令解释，而不是被当成口令
    assert.deepEqual(textOf(session.run('ls')), ['documents/  pictures/'])
  })

  test('邮箱提示被打断后仍停在邮箱阶段', () => {
    const session = makeSession()
    passThrough(session)
    session.cancel()
    assert.equal(session.state().mode, 'email')
    assert.equal(session.state().passed, false)
  })
})

describe('历史与计数', () => {
  test('历史只记命令，不记口令', () => {
    const session = makeSession()
    const password = passwordOf(session)
    session.run('ls')
    session.run('su self')
    session.run(password)
    assert.deepEqual(session.state().history, ['ls', 'su self'])
  })

  test('历史是快照，外部改动不影响内部', () => {
    const session = makeSession()
    session.run('ls')
    session.state().history.push('fake')
    assert.deepEqual(session.state().history, ['ls'])
  })

  test('命令计数用于提交内容里的 commands_used', () => {
    const session = makeSession()
    session.run('ls')
    session.run('cat documents/password')
    assert.equal(session.state().commands, 2)
  })
})

describe('载荷编解码（构建与页面共用）', () => {
  test('往返一致', () => {
    const payload = JSON.stringify({ 中文: '内容', n: 1827694 })
    const encoded = core.encodePayload(payload, 'k3y-密钥')
    assert.equal(core.decodePayload(encoded, 'k3y-密钥'), payload)
  })

  test('编码结果里不含明文', () => {
    const encoded = core.encodePayload('password: facb251ac3e8c06d', 'secret-key')
    assert.ok(!encoded.includes('facb'))
    assert.ok(!encoded.includes('password'))
  })

  test('换行与空格会被忽略（载荷可以折行存放）', () => {
    const encoded = core.encodePayload('hello 世界', 'k')
    const wrapped = encoded.replace(/(.{8})/g, '$1\n  ')
    assert.equal(core.decodePayload(wrapped, 'k'), 'hello 世界')
  })

  test('密钥不对时解出来是别的东西，但不会抛', () => {
    const encoded = core.encodePayload('hello', 'right')
    assert.notEqual(core.decodePayload(encoded, 'wrong'), 'hello')
  })
})

describe('源码结构（构建脚本依赖的锚点）', () => {
  const html = readSource()

  test('四个块都在：fs / core / sdk / app', () => {
    for (const id of ['puzzle-fs', 'puzzle-core', 'cea-sdk', 'puzzle-app']) {
      assert.ok(html.includes(`id="${id}"`), `缺少 id="${id}"`)
    }
  })

  test('FS_KEY 锚点必须原样存在 —— 构建按它注入密钥', () => {
    assert.ok(html.includes("var FS_KEY = ''"), "构建锚点 `var FS_KEY = ''` 不见了")
  })

  test('FS_KEY 锚点在源码里只能出现一次', () => {
    /*
      注释里再抄一遍锚点原文，构建的字符串替换就会命中注释那一处，密钥注不进去，
      页面上表现为"内容读取失败：JSON 解析出错"。这条断言把那次的坑钉住。
    */
    const hits = (html.match(/var FS_KEY =/g) || []).length
    assert.equal(hits, 1, `var FS_KEY = 出现了 ${hits} 次，必须恰好 1 次`)
  })

  test('SDK 那一行必须带 id（判断只看 id，不看 src）', () => {
    assert.match(html, /<script src="\/sdk\/v1\/cea\.js" id="cea-sdk"><\/script>/)
  })
})
