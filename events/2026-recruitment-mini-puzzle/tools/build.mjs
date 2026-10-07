/**
 * 构建投放包：混淆压缩 JS、编码文件系统数据、打出可上传的 zip。
 *
 *   npm run build      （在 events/2026-recruitment-mini-puzzle 下）
 *
 * 产物（dist/，不入库）：
 *   index.html                          投放入口，JS 已混淆
 *   pictures/illusion.png               download 的目标（真实文件）
 *   2026-recruitment-mini-puzzle.zip    上传这个
 *
 * ## 为什么 documents/ 不进投放包
 *
 * `ls` 会把 `documents/password` 这个**路径**告诉玩家，而 /content/** 是公开只读的 ——
 * 只要文件真在包里，手打一次 URL 就绕过整个谜题。所以 documents/ 下的内容只是**构建输入**：
 * 它们被编码后内联进产物，运行时才还原成终端里的"文件"。
 *
 * ## 混淆不是安全边界
 *
 * `cat documents/password` 必须打印明文，所以口令在运行时一定存在于页面内存里。这层
 * 混淆挡的是"直接读源码抄答案"，挡不住有心反混淆的人 —— 构建期那几条断言也只保证
 * **静态文件里**没有明文。
 */
import { randomBytes } from 'node:crypto'
import { copyFileSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

import JavaScriptObfuscator from 'javascript-obfuscator'

import { ROOT, extractBlock, loadCore, readSource } from './source.mjs'
import { createZip } from './zip.mjs'

const DIST = join(ROOT, 'dist')
const PICTURES = join(ROOT, 'pictures')
const DOCUMENTS = join(ROOT, 'documents')
const ZIP_NAME = '2026-recruitment-mini-puzzle.zip'

/**
 * 混淆参数。取舍写在每一项旁边 —— 这些开关不是"越多越安全"，它们只影响**阅读成本**。
 */
const OBFUSCATOR_OPTIONS = {
  compact: true,
  target: 'browser',

  // 字面量收进一张编码过的字符串表：答案不再以明文散落在代码里
  stringArray: true,
  stringArrayThreshold: 1,
  stringArrayEncoding: ['base64'],
  stringArrayRotate: true,
  stringArrayShuffle: true,
  splitStrings: true,
  splitStringsChunkLength: 6,

  identifierNamesGenerator: 'hexadecimal',
  renameGlobals: false,

  controlFlowFlattening: true,
  controlFlowFlatteningThreshold: 0.75,
  deadCodeInjection: true,
  deadCodeInjectionThreshold: 0.3,

  // 产物被改一个字符就自毁。代价是"手改产物"不可行 —— 本来也不该那么做（改源码重跑构建）
  selfDefending: true,

  /*
    刻意**不开** debugProtection：它会拖住 devtools，对正常排障不友好；而这一层本来
    就不承担安全职责。同理不开 disableConsoleOutput —— 活动页出错时控制台是唯一的线索。
  */
  debugProtection: false,
  disableConsoleOutput: false,
  unicodeEscapeSequence: false,
}

const blockPattern = (id) => new RegExp(`<script id="${id}"[^>]*>[\\s\\S]*?</script>`)

/** 把三个块换成占位标记，剩下的就是"外壳" */
function splitShell(html) {
  const shell = html
    .replace(blockPattern('puzzle-fs'), '@@CEA_FS@@')
    .replace(blockPattern('puzzle-core'), '@@CEA_CORE@@')
    .replace(blockPattern('puzzle-app'), '@@CEA_APP@@')

  for (const marker of ['@@CEA_FS@@', '@@CEA_CORE@@', '@@CEA_APP@@']) {
    const hits = shell.split(marker).length - 1
    if (hits !== 1) throw new Error(`占位标记 ${marker} 出现 ${hits} 次，应当恰好 1 次`)
  }
  return shell
}

/**
 * 清掉外壳里的注释。
 *
 * 注释是给读源码的人看的（构建方式、为什么这么做），而产物要给玩家看 —— 留着等于把
 * 设计说明连同"答案在哪"一起送出去。脚本块此时已经被占位符换掉，所以这里的正则碰不到 JS。
 */
function stripComments(shell) {
  return shell
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/[ \t]+$/gm, '')
    .replace(/\n{3,}/g, '\n\n')
}

export function build() {
  const html = readSource()
  const core = loadCore(html)

  // ---- 文件系统数据：校验、编码、折行 ----
  const fsText = extractBlock(html, 'puzzle-fs').trim()
  const fsData = JSON.parse(fsText)

  /*
    结构校验按**谜题本身**来写，而不是"有个 tree 就算数"：
    bobby 的家目录里必须有那份口令，self 的家目录里必须有那张图。
    少了任何一个，谜题就不再是"探索目录 → 找到口令 → 切用户 → 看到 self 的东西"。
  */
  const bobby = fsData.homes && fsData.homes.bobby
  const self = fsData.homes && fsData.homes.self
  if (!bobby || !self) throw new Error('文件系统里缺少 homes.bobby / homes.self —— 谜题结构不对')
  if (!bobby.documents || !bobby.documents.children || !bobby.documents.children.password) {
    throw new Error('bobby 的家目录里没有 documents/password —— 谜题解不下去')
  }
  if (!bobby.pictures || Object.keys(bobby.pictures.children || {}).length !== 0) {
    throw new Error('bobby 的 pictures 必须是空的 —— 图片在 self 那边')
  }
  if (!self.pictures || !self.pictures.children || !self.pictures.children['illusion.png']) {
    throw new Error('self 的家目录里没有 pictures/illusion.png')
  }
  if (!self.documents || !self.documents.children || !self.documents.children.data) {
    throw new Error('self 的家目录里没有 documents/data')
  }

  const key = randomBytes(24).toString('base64url')
  const payload = core.encodePayload(fsText, key)

  // 往返一次：编码器与解码器必须对得上，否则产物在浏览器里就是一具空壳
  if (core.decodePayload(payload, key) !== fsText) {
    throw new Error('载荷往返不一致：编码器与解码器已经漂移')
  }

  const wrappedPayload = (payload.match(/.{1,96}/g) || []).join('\n')

  // ---- 脚本：注入密钥 -> 合并 -> 混淆 ----
  const coreSource = extractBlock(html, 'puzzle-core')
  const appSource = extractBlock(html, 'puzzle-app')

  /*
    密钥注入。这里的三道检查都是被同一个 bug 逼出来的：

    `String.replace` 只换**第一处**。如果注释里也写了锚点原文，密钥就会注进注释里，
    真正的变量还是空串 —— 空密钥解出来的是一堆乱码，页面报"JSON 解析失败"，而产物
    每一项看起来都对。所以：锚点必须唯一、替换必须发生、替换后必须恰好一处赋值。
  */
  const anchor = 'var FS_KEY ='
  const anchorCount = (appSource.match(/var FS_KEY =/g) || []).length
  if (anchorCount !== 1) {
    throw new Error(
      `应用块里 ${anchor} 出现了 ${anchorCount} 次，必须恰好 1 次 —— ` +
        '注释里重复一遍锚点原文，就会让密钥注到注释里去',
    )
  }

  const assignment = `${anchor} ${JSON.stringify(key)}`
  const appWithKey = appSource.replace(/var FS_KEY = ''/, assignment)
  if (!appWithKey.includes(assignment) || appWithKey.includes("var FS_KEY = ''")) {
    throw new Error('密钥注入没有生效：应用块里仍是空密钥')
  }
  if ((appWithKey.match(/var FS_KEY =/g) || []).length !== 1) {
    throw new Error('注入后 var FS_KEY = 不止一处')
  }

  /*
    外层再套一个 IIFE：`var PuzzleCore` 因此变成函数作用域的局部变量，混淆器会把它
    改名掉。不套的话它是全局变量 —— 源码结构会留在产物里，"PuzzleCore" 这个名字
    在控制台里还是个现成的抓手（虽然拿不到什么秘密，但没必要留着）。

    两段之间**必须有分号**：在源码里它们是两个 <script>，不写分号没事；一旦拼成一段，
    `var X = (function(){…})()` 后面紧跟 `(function(){…})()` 会被 ASI 当成**调用**，
    报的是 "(intermediate value)(...) is not a function"，而位置指向混淆后的偏移量，
    极难反查。踩过一次，所以这里显式加分号，源码里也各自收尾。
  */
  const bundleSource = `(function(){\n${coreSource}\n;\n${appWithKey}\n;\n})()`

  const obfuscated = JavaScriptObfuscator.obfuscate(
    bundleSource,
    OBFUSCATOR_OPTIONS,
  ).getObfuscatedCode()

  if (obfuscated.includes('</script')) {
    // 真出现的话产物会被浏览器截断，而且断得很难查
    throw new Error('混淆产物里出现了 </script，会截断内联脚本')
  }

  // ---- 拼装产物 ----
  let output = stripComments(splitShell(html))
  output = output.replace(
    '@@CEA_FS@@',
    `<script id="puzzle-fs" type="application/octet-stream" data-enc="1">\n${wrappedPayload}\n</script>`,
  )
  /*
    合成的一束插在 **app 原来的位置**（即 SDK 之后），而不是 core 的位置。
    app 启动时要读 window.CEA —— 插到 SDK 之前的话，页面会直接走到"页面没能初始化"，
    而症状看起来像"混淆把页面搞坏了"，排查方向会完全错。
    tests/build.spec.mjs 有一条断言钉着这个先后顺序。
  */
  output = output.replace('@@CEA_CORE@@', '')
  output = output.replace('@@CEA_APP@@', `<script id="puzzle-bundle">\n${obfuscated}\n</script>`)

  // ---- 构建期断言：静态文件里不许出现答案 ----
  const secrets = collectSecrets()
  const leaks = secrets.filter((secret) => output.includes(secret))
  if (leaks.length > 0) {
    throw new Error(
      `产物里出现了明文（${leaks.length} 处）：${leaks
        .map((secret) => JSON.stringify(secret.slice(0, 24)))
        .join('、')}\n` +
        '怀疑是注释没清掉、或者文件内容被直接内联了。构建必须失败 —— 这层保护一旦静默失效，谜题就只剩"看一眼源码"。',
    )
  }

  for (const required of ['id="puzzle-fs"', 'data-enc="1"', 'id="cea-sdk"', 'id="puzzle-bundle"']) {
    if (!output.includes(required)) throw new Error(`产物里缺少 ${required}`)
  }

  /*
    变量名必须消失：`PuzzleCore` 是套上外层 IIFE 后应当被改名的局部名，`FS_KEY` 是密钥
    锚点。它们出现就说明外包装或改名没生效。

    刻意**不查** `decodePayload` 这类**属性名** —— 混淆器默认不改对象键，而它们只是
    闭包内部的接口名，不构成泄露。查它们只会让构建在正确的时候失败。
  */
  for (const forbidden of ['PuzzleCore', 'FS_KEY']) {
    if (output.includes(forbidden)) {
      throw new Error(`产物里出现了内部变量名 ${forbidden} —— 外层 IIFE 没套上，或改名没生效`)
    }
  }

  // ---- 落盘 ----
  rmSync(DIST, { recursive: true, force: true })
  mkdirSync(join(DIST, 'pictures'), { recursive: true })
  writeFileSync(join(DIST, 'index.html'), output, 'utf8')
  copyFileSync(join(PICTURES, 'illusion.png'), join(DIST, 'pictures', 'illusion.png'))

  /*
    投放包只放两个条目：入口与那张图。
    documents/ **刻意不在其中** —— 它只是构建输入（见文件头）。
  */
  const zip = createZip([
    { name: 'index.html', data: Buffer.from(output, 'utf8') },
    { name: 'pictures/illusion.png', data: readFileSync(join(PICTURES, 'illusion.png')) },
  ])
  writeFileSync(join(DIST, ZIP_NAME), zip)

  return {
    distDir: DIST,
    htmlPath: join(DIST, 'index.html'),
    zipPath: join(DIST, ZIP_NAME),
    key,
    payload,
    fsText,
    html: output,
    obfuscatedSize: Buffer.byteLength(obfuscated, 'utf8'),
    zip,
  }
}

/**
 * 从**真实文件**里现取答案，而不是在构建脚本里再抄一份。
 *
 * 这样改了 documents/ 下的东西之后，断言自动跟着变 —— 抄一份常量的话，改了源文件、
 * 忘了改常量，构建就会"通过"一个已经泄露的产物。
 *
 * 走整个目录而不是写死几个文件名：新增一个文件（比如后来的 documents/data）如果漏了，
 * 断言就会静默失效，而"少检一个文件"是看不出来的。
 */
function collectSecrets() {
  const secrets = []
  const documents = join(ROOT, 'documents')

  for (const entry of readdirSync(documents, { withFileTypes: true })) {
    if (!entry.isFile()) continue
    const content = readFileSync(join(documents, entry.name), 'utf8')
    secrets.push(content)

    /*
      文件名只把**不像代码标识符**的那些当秘密。`password`、`data` 这类词本身就会
      出现在代码里（路径片段、`data-enc` 属性），拿它们做断言只会让构建在正确的时候
      失败 —— 而中文名只可能来自文件系统数据，出现就说明载荷没编码。

      真正的答案是**内容**，它无论如何都会被检。
    */
    if (/[^\x00-\x7F]/.test(entry.name)) secrets.push(entry.name.replace(/\.[^.]+$/, ''))

    for (const line of content.split(/\r?\n/)) {
      if (line.trim()) secrets.push(line.trim())
    }
  }
  return secrets
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const result = build()
  const kib = (bytes) => (bytes / 1024).toFixed(1) + ' KiB'
  console.log('构建完成：')
  console.log('  混淆后脚本  ' + kib(result.obfuscatedSize))
  console.log('  dist/index.html  ' + kib(Buffer.byteLength(result.html, 'utf8')))
  console.log('  ' + ZIP_NAME + '  ' + kib(result.zip.length))
  console.log('  明文断言    通过（' + collectSecrets().length + ' 个待检字符串都不在产物里）')
}
