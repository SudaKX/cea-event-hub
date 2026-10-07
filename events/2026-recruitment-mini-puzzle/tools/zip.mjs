/**
 * 极小的 zip 写入/读取。
 *
 * 为什么不用现成的库：整个活动页只有两个条目，而投放包必须能被后端的
 * `ContentService`（Python zipfile）正确解开。自己写 60 行比引入一个依赖更容易看清
 * 字节长什么样 —— 也正因为如此，tests/build.spec.mjs 里配了一个**读取端**做自洽检查，
 * 验证时还会用 Python 的 zipfile 再解一遍（那是另一个实现，才算独立验证）。
 *
 * 约束来自后端的校验（backend/app/services/content.py）：
 *   条目名不得以 / 或 \ 开头、不得含 `:`、不得含 `..`、不得是符号链接。
 * 这里写出来的条目全是普通文件，且显式带 UTF-8 标记位（0x0800）。
 */
import { deflateRawSync } from 'node:zlib'

const LOCAL_HEADER = 0x04034b50
const CENTRAL_HEADER = 0x02014b50
const EOCD = 0x06054b50
const FLAG_UTF8 = 0x0800
const METHOD_DEFLATE = 8

/** 固定时间戳：构建产物要可复现，不该因为"什么时候跑的"而变 */
const DOS_TIME = 0 // 00:00:00
const DOS_DATE = ((2026 - 1980) << 9) | (1 << 5) | 1 // 2026-01-01

const CRC_TABLE = (() => {
  const table = new Int32Array(256)
  for (let i = 0; i < 256; i++) {
    let value = i
    for (let bit = 0; bit < 8; bit++) {
      value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1
    }
    table[i] = value
  }
  return table
})()

function crc32(buffer) {
  let crc = -1
  for (let i = 0; i < buffer.length; i++) {
    crc = (crc >>> 8) ^ CRC_TABLE[(crc ^ buffer[i]) & 0xff]
  }
  return (crc ^ -1) >>> 0
}

function assertSafeName(name) {
  if (!name || name.startsWith('/') || name.startsWith('\\')) {
    throw new Error(`非法条目名：${name}`)
  }
  if (name.includes(':') || name.split('/').includes('..')) {
    throw new Error(`非法条目名：${name}`)
  }
}

/**
 * @param entries [{ name, data: Buffer }]
 * @returns Buffer
 */
export function createZip(entries) {
  const chunks = []
  const central = []
  let offset = 0

  for (const entry of entries) {
    assertSafeName(entry.name)
    const nameBytes = Buffer.from(entry.name, 'utf8')
    const raw = entry.data
    const deflated = deflateRawSync(raw, { level: 9 })
    // 压不动就别压（PNG 这类已经是压缩格式），并存方式也得支持
    const useDeflate = deflated.length < raw.length
    const body = useDeflate ? deflated : raw
    const method = useDeflate ? METHOD_DEFLATE : 0
    const crc = crc32(raw)

    const local = Buffer.alloc(30)
    local.writeUInt32LE(LOCAL_HEADER, 0)
    local.writeUInt16LE(20, 4) // version needed
    local.writeUInt16LE(FLAG_UTF8, 6)
    local.writeUInt16LE(method, 8)
    local.writeUInt16LE(DOS_TIME, 10)
    local.writeUInt16LE(DOS_DATE, 12)
    local.writeUInt32LE(crc, 14)
    local.writeUInt32LE(body.length, 18)
    local.writeUInt32LE(raw.length, 22)
    local.writeUInt16LE(nameBytes.length, 26)
    local.writeUInt16LE(0, 28) // extra length
    chunks.push(local, nameBytes, body)

    const dir = Buffer.alloc(46)
    dir.writeUInt32LE(CENTRAL_HEADER, 0)
    dir.writeUInt16LE(20, 4) // version made by
    dir.writeUInt16LE(20, 6) // version needed
    dir.writeUInt16LE(FLAG_UTF8, 8)
    dir.writeUInt16LE(method, 10)
    dir.writeUInt16LE(DOS_TIME, 12)
    dir.writeUInt16LE(DOS_DATE, 14)
    dir.writeUInt32LE(crc, 16)
    dir.writeUInt32LE(body.length, 20)
    dir.writeUInt32LE(raw.length, 24)
    dir.writeUInt16LE(nameBytes.length, 28)
    dir.writeUInt16LE(0, 30) // extra
    dir.writeUInt16LE(0, 32) // comment
    dir.writeUInt16LE(0, 34) // disk
    dir.writeUInt16LE(0, 36) // internal attrs
    dir.writeUInt32LE(0, 38) // external attrs：0 = 普通文件，不是符号链接
    dir.writeUInt32LE(offset, 42)
    central.push(dir, nameBytes)

    offset += local.length + nameBytes.length + body.length
  }

  const centralBuffer = Buffer.concat(central)
  const end = Buffer.alloc(22)
  end.writeUInt32LE(EOCD, 0)
  end.writeUInt16LE(0, 4)
  end.writeUInt16LE(0, 6)
  end.writeUInt16LE(entries.length, 8)
  end.writeUInt16LE(entries.length, 10)
  end.writeUInt32LE(centralBuffer.length, 12)
  end.writeUInt32LE(offset, 16)
  end.writeUInt16LE(0, 20)

  return Buffer.concat([...chunks, centralBuffer, end])
}

/** 从中央目录读回条目清单 —— 写入端与读取端分开写，才能互相挑错 */
export function readZipEntries(buffer) {
  const endOffset = buffer.lastIndexOf(Buffer.from([0x50, 0x4b, 0x05, 0x06]))
  if (endOffset < 0) throw new Error('不是 zip：找不到 EOCD')

  const count = buffer.readUInt16LE(endOffset + 10)
  let cursor = buffer.readUInt32LE(endOffset + 16)
  const entries = []

  for (let i = 0; i < count; i++) {
    if (buffer.readUInt32LE(cursor) !== CENTRAL_HEADER) throw new Error('中央目录头不对')
    const method = buffer.readUInt16LE(cursor + 10)
    const crc = buffer.readUInt32LE(cursor + 16)
    const compressedSize = buffer.readUInt32LE(cursor + 20)
    const size = buffer.readUInt32LE(cursor + 24)
    const nameLength = buffer.readUInt16LE(cursor + 28)
    const extraLength = buffer.readUInt16LE(cursor + 30)
    const commentLength = buffer.readUInt16LE(cursor + 32)
    const externalAttrs = buffer.readUInt32LE(cursor + 38)
    const name = buffer.toString('utf8', cursor + 46, cursor + 46 + nameLength)

    entries.push({ name, method, crc, size, compressedSize, externalAttrs })
    cursor += 46 + nameLength + extraLength + commentLength
  }

  return entries
}
