/**
 * 诊断包纯函数：脱敏 + 打 zip。
 * 不依赖 electron 运行时（只依赖 Node Buffer / TextEncoder），便于 vitest 直接测。
 *
 * 硬约束：产物中禁止出现 Cookie / Token 原文（设置快照与日志尾部均过脱敏）。
 */

export const LOG_TAIL_LINES = 500

/** 敏感键：命中则整值替换为 <redacted>（大小写不敏感，子串匹配）。 */
const SENSITIVE_KEY_RE =
  /(cookie|token|secret|password|passwd|authorization|auth[_-]?key|api[_-]?key|session|credential|set-cookie|ws_secret|wssecret|access[_-]?token|refresh[_-]?token|sessdata|bili_jct|csrf)/i

/** 行内凭据：`token=...` / `api_key: ...` 等（值可能含 `;` 分号对）。 */
const INLINE_SECRET_RE = new RegExp(
  String.raw`(cookie|set-cookie|authorization|proxy-authorization|token|access_token|auth_token|refresh_token|api_key|apikey|secret|password|passwd|sessdata|bili_jct|csrf|signature|ws_secret|wssecret)` +
    String.raw`(\s*[:=]\s*)([^\s,;]+(?:;\s*[^\s,;]+)*)`,
  'gi',
)

/** HTTP 头整行 Cookie/Authorization 块（含 FFmpeg -headers 里的转义 CRLF）。 */
const HEADER_BLOB_RE = new RegExp(
  String.raw`((?:cookie|set-cookie|authorization|proxy-authorization|referer)\s*[:=]\s*)(.*?)(?=(?:\\r\\n|\r\n|\n|$))`,
  'gis',
)

export const REDACTED = '<redacted>'

/** 脱敏任意文本：整头块 + 行内凭据值。 */
export function redactText(input: string): string {
  if (!input) return ''
  let out = input.replace(HEADER_BLOB_RE, (_m, prefix: string) => `${prefix}${REDACTED}`)
  out = out.replace(INLINE_SECRET_RE, (_m, key: string, sep: string) => `${key}${sep}${REDACTED}`)
  return out
}

/** 递归脱敏设置对象：敏感键整值打码，字符串值再过 redactText。 */
export function sanitizeSettings(value: unknown): unknown {
  if (value === null || value === undefined) return value
  if (Array.isArray(value)) return value.map((item) => sanitizeSettings(item))
  if (typeof value === 'object') {
    const out: Record<string, unknown> = {}
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      if (SENSITIVE_KEY_RE.test(key)) {
        out[key] = REDACTED
      } else {
        out[key] = sanitizeSettings(item)
      }
    }
    return out
  }
  if (typeof value === 'string') return redactText(value)
  return value
}

/** 取文本尾部 N 行（不足则整段）。 */
export function tailLines(text: string, lines: number = LOG_TAIL_LINES): string {
  if (!text) return ''
  const all = text.split('\n')
  const n = lines > 0 ? lines : LOG_TAIL_LINES
  return all.slice(-n).join('\n')
}

/** 脱敏后的日志尾部。 */
export function sanitizeLogTail(text: string, lines: number = LOG_TAIL_LINES): string {
  return redactText(tailLines(text, lines))
}

export interface DiagnosticManifest {
  version: string
  platform: string
  windowsStore: boolean
  generatedAt: string
}

export function buildManifest(meta: {
  version: string
  platform: string
  windowsStore: boolean
  generatedAt?: string
}): DiagnosticManifest {
  return {
    version: meta.version,
    platform: meta.platform,
    windowsStore: !!meta.windowsStore,
    generatedAt: meta.generatedAt ?? new Date().toISOString(),
  }
}

export interface ZipEntry {
  name: string
  data: Buffer | string
}

// ---- 最小 ZIP（store，不压缩）----
// 避免引入第三方 zip 依赖；诊断包体积小，store 足够。

const CRC_TABLE = (() => {
  const table = new Int32Array(256)
  for (let i = 0; i < 256; i++) {
    let c = i
    for (let k = 0; k < 8; k++) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    }
    table[i] = c
  }
  return table
})()

function crc32(buf: Buffer): number {
  let c = -1
  for (let i = 0; i < buf.length; i++) {
    c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8)
  }
  return (c ^ -1) >>> 0
}

function dosDateTime(date: Date): { time: number; date: number } {
  const time =
    (date.getHours() << 11) | (date.getMinutes() << 5) | (Math.floor(date.getSeconds() / 2) & 0x1f)
  const dosDate =
    ((date.getFullYear() - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate()
  return { time, date: dosDate }
}

/** 将若干文本条目打成 zip（store）。返回完整 zip 字节。 */
export function buildZipStore(entries: ZipEntry[], now: Date = new Date()): Buffer {
  const { time, date } = dosDateTime(now)
  const locals: Buffer[] = []
  const centrals: Buffer[] = []
  let offset = 0

  for (const entry of entries) {
    const nameBuf = Buffer.from(entry.name, 'utf-8')
    const dataBuf = Buffer.isBuffer(entry.data) ? entry.data : Buffer.from(entry.data, 'utf-8')
    const crc = crc32(dataBuf)

    const local = Buffer.alloc(30 + nameBuf.length)
    local.writeUInt32LE(0x04034b50, 0)
    local.writeUInt16LE(20, 4) // version needed
    local.writeUInt16LE(0x0800, 6) // UTF-8 names
    local.writeUInt16LE(0, 8) // method: store
    local.writeUInt16LE(time, 10)
    local.writeUInt16LE(date, 12)
    local.writeUInt32LE(crc, 14)
    local.writeUInt32LE(dataBuf.length, 18)
    local.writeUInt32LE(dataBuf.length, 22)
    local.writeUInt16LE(nameBuf.length, 26)
    local.writeUInt16LE(0, 28)
    nameBuf.copy(local, 30)

    const central = Buffer.alloc(46 + nameBuf.length)
    central.writeUInt32LE(0x02014b50, 0)
    central.writeUInt16LE(20, 4) // version made by
    central.writeUInt16LE(20, 6) // version needed
    central.writeUInt16LE(0x0800, 8)
    central.writeUInt16LE(0, 10)
    central.writeUInt16LE(time, 12)
    central.writeUInt16LE(date, 14)
    central.writeUInt32LE(crc, 16)
    central.writeUInt32LE(dataBuf.length, 20)
    central.writeUInt32LE(dataBuf.length, 24)
    central.writeUInt16LE(nameBuf.length, 28)
    central.writeUInt16LE(0, 30) // extra
    central.writeUInt16LE(0, 32) // comment
    central.writeUInt16LE(0, 34) // disk
    central.writeUInt16LE(0, 36) // internal attrs
    central.writeUInt32LE(0, 38) // external attrs
    central.writeUInt32LE(offset, 42)
    nameBuf.copy(central, 46)

    locals.push(local, dataBuf)
    centrals.push(central)
    offset += local.length + dataBuf.length
  }

  const centralStart = offset
  const centralBuf = Buffer.concat(centrals)
  const eocd = Buffer.alloc(22)
  eocd.writeUInt32LE(0x06054b50, 0)
  eocd.writeUInt16LE(0, 4)
  eocd.writeUInt16LE(0, 6)
  eocd.writeUInt16LE(entries.length, 8)
  eocd.writeUInt16LE(entries.length, 10)
  eocd.writeUInt32LE(centralBuf.length, 12)
  eocd.writeUInt32LE(centralStart, 16)
  eocd.writeUInt16LE(0, 20)

  return Buffer.concat([...locals, centralBuf, eocd])
}

/** 组装诊断包条目（日志尾部 + manifest + 设置快照，全部脱敏）。 */
export function buildDiagnosticEntries(input: {
  manifest: DiagnosticManifest
  settingsSnapshot: unknown
  logs: Array<{ name: string; content: string }>
  logTailLines?: number
}): ZipEntry[] {
  const n = input.logTailLines ?? LOG_TAIL_LINES
  const entries: ZipEntry[] = [
    {
      name: 'manifest.json',
      data: JSON.stringify(input.manifest, null, 2),
    },
    {
      name: 'settings.sanitized.json',
      data: JSON.stringify(sanitizeSettings(input.settingsSnapshot), null, 2),
    },
  ]
  for (const log of input.logs) {
    entries.push({
      name: `logs/${log.name}`,
      data: sanitizeLogTail(log.content, n),
    })
  }
  return entries
}
