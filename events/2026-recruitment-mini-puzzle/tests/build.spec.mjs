/**
 * 构建产物的测试。
 *
 * 这一组守的是"投放出去的那份东西"：它必须还能跑（结构齐全、载荷能解回来），
 * 也必须不再泄露答案。前者靠结构断言，后者靠**从真实文件里现取明文去搜**。
 *
 * 这里会真的跑一遍构建（几十秒内），因为验证对象就是构建结果本身；对 dist/ 的
 * 覆盖写是可接受的 —— 它是产物目录，不入库。
 */
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { before, describe, test } from 'node:test'

import { build } from '../tools/build.mjs'
import { readZipEntries } from '../tools/zip.mjs'
import { ROOT, loadCore } from '../tools/source.mjs'

let result
let html

before(() => {
  result = build()
  html = readFileSync(result.htmlPath, 'utf8')
})

describe('产物结构', () => {
  test('入口、载荷块、混淆脚本、SDK 引用都在', () => {
    for (const needle of [
      'id="puzzle-fs"',
      'data-enc="1"',
      'id="puzzle-bundle"',
      '<script src="/sdk/v1/cea.js" id="cea-sdk"></script>',
      '</html>',
    ]) {
      assert.ok(html.includes(needle), `产物里缺少 ${needle}`)
    }
  })

  test('源码注释被清掉', () => {
    assert.equal(html.includes('<!--'), false)
    assert.equal(html.includes('/*'), false)
  })

  test('内部变量名不出现（外层 IIFE 与改名生效）', () => {
    // 属性名（如 createSession / decodePayload）混淆器默认不改，也不该改 —— 它们只是闭包内部接口名
    for (const name of ['PuzzleCore', 'FS_KEY', 'themed']) {
      assert.equal(html.includes(name), false, `产物里还有 ${name}`)
    }
  })

  test('没有多出来的 </script>（否则内联脚本会被截断）', () => {
    const opens = html.match(/<script\b/g) || []
    const closes = html.match(/<\/script>/g) || []
    assert.equal(opens.length, closes.length)
    assert.equal(closes.length, 3) // 载荷 + 混淆包 + SDK
  })

  test('混淆包排在 SDK 之后 —— 启动要读 window.CEA', () => {
    const sdk = html.indexOf('id="cea-sdk"')
    const bundle = html.indexOf('id="puzzle-bundle"')
    assert.ok(sdk > 0 && bundle > 0)
    assert.ok(sdk < bundle, 'SDK 必须先于混淆包，否则页面会停在"没能初始化"')
  })
})

describe('答案不在产物里', () => {
  /**
   * 从真实文件现取，避免"改了源文件忘了改断言"。
   *
   * 走整个 documents/ 目录：以后往里加文件时，如果这条断言只写死了旧的两个名字，
   * 新文件就会**静默不受检** —— 而那正是它该守住的地方。
   */
  function plaintexts() {
    const documents = join(ROOT, 'documents')
    const secrets = []
    for (const entry of readdirSync(documents, { withFileTypes: true })) {
      if (!entry.isFile()) continue
      const content = readFileSync(join(documents, entry.name), 'utf8')
      secrets.push(content)
      // 中文名只可能来自文件系统数据，算秘密；password / data 这类词本身就在代码里
      if (/[^\x00-\x7F]/.test(entry.name)) secrets.push(entry.name.replace(/\.[^.]+$/, ''))
      secrets.push(...content.split(/\r?\n/).filter((line) => line.trim()))
    }
    return secrets
  }

  test('口令、文件名、正文（含 documents/data）都不在产物里', () => {
    const leaked = plaintexts().filter((text) => html.includes(text))
    assert.deepEqual(leaked, [], '产物里出现了明文：' + leaked.join(' | '))
  })

  test('documents/data 的正文确实被检到了（否则上面那条是空转）', () => {
    const data = readFileSync(join(ROOT, 'documents', 'data'), 'utf8').trim()
    assert.ok(plaintexts().includes(data), 'documents/data 的内容没进待检清单')
  })

  test('密钥不在产物里（它以字符串数组的形式被编码）', () => {
    assert.equal(html.includes(result.key), false)
  })

  test('载荷块里只有 base64 与空白', () => {
    const payload = /<script id="puzzle-fs"[^>]*>([\s\S]*?)<\/script>/.exec(html)[1]
    assert.match(payload.trim(), /^[A-Za-z0-9+/=\s]+$/)
  })
})

describe('载荷能解回原样', () => {
  test('用产物里的载荷与构建密钥解出的就是源文件那份 JSON', () => {
    const payload = /<script id="puzzle-fs"[^>]*>([\s\S]*?)<\/script>/.exec(html)[1]
    const decoded = loadCore().decodePayload(payload, result.key)
    assert.deepEqual(JSON.parse(decoded), JSON.parse(result.fsText))
  })

  test('解出来的内容能支撑完整解谜路径（两个用户的家目录都对）', () => {
    const payload = /<script id="puzzle-fs"[^>]*>([\s\S]*?)<\/script>/.exec(html)[1]
    const fs = JSON.parse(loadCore().decodePayload(payload, result.key))

    const bobby = fs.homes.bobby
    assert.ok(bobby.documents.children['password'].content.includes('username: self'))
    assert.equal(bobby.documents.children['文本文档.txt'].size, 43)
    // bobby 的 pictures 必须是空的：那张图是"换成 self 之后才看得到"的东西
    assert.deepEqual(Object.keys(bobby.pictures.children), [])

    const self = fs.homes.self
    assert.equal(self.pictures.children['illusion.png'].size, 1827694)
    assert.equal(self.pictures.children['illusion.png'].binary, true)
    assert.equal(self.documents.children['data'].size, 114)
    assert.match(self.documents.children['data'].content, /^意外？抛弃？/)
  })
})

describe('投放包', () => {
  function entries() {
    return readZipEntries(result.zip)
  }

  test('只有入口与那张图两个条目 —— documents/ 不进包', () => {
    assert.deepEqual(
      entries().map((entry) => entry.name).sort(),
      ['index.html', 'pictures/illusion.png'],
    )
  })

  test('条目都是普通文件，没有符号链接（后端会直接拒绝）', () => {
    for (const entry of entries()) {
      assert.equal(entry.externalAttrs >> 16, 0, entry.name)
    }
  })

  test('入口内容与 dist/index.html 一致，大小对得上', () => {
    const entry = entries().find((item) => item.name === 'index.html')
    assert.equal(entry.size, Buffer.byteLength(html, 'utf8'))
  })

  test('图片是原始字节（1.8 MB，没有被改动）', () => {
    const entry = entries().find((item) => item.name === 'pictures/illusion.png')
    assert.equal(entry.size, 1827694)
  })

  test('dist 里没有 documents/ —— 它只是构建输入', () => {
    assert.throws(() => readFileSync(join(result.distDir, 'documents', 'password')))
  })
})
