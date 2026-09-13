/**
 * 本地媒体文件读取（方案 A / §3.1）——纯函数校验模块。
 *
 * 回看不再经后端 FFmpeg 转码流，而是渲染进程通过 IPC 直接按字节读取本地录制文件；
 * 本模块只承载「请求是否合法、路径是否落在白名单根之下」这类可单测的判定，
 * 不依赖 electron 运行时（只依赖 node 的 fs / path），便于用 node 直接自检。
 *
 * 设计约定：
 * - 所有函数都不抛异常，失败统一返回 { ok: false, error }；
 * - Windows 下路径比较大小写不敏感，且必须做「分隔符边界匹配」，
 *   否则根 "D:\a-b" 会误命中 "D:\a-bc"（前缀伪装）；
 * - 路径必须同时通过「字面 resolve 结果」与「realpath 结果」的白名单判定，
 *   防止白名单目录内的 junction / symlink 指向外部（与 main.ts 既有
 *   _isSafePath 的 AND 语义一致）。
 */
import fs from 'fs'
import path from 'path'

/** 允许读取的媒体扩展名白名单（大小写不敏感）。 */
export const ALLOWED_MEDIA_EXTENSIONS = ['.mp4', '.mkv', '.flv', '.mov', '.ts', '.m4s', '.m4v'] as const

/** 单次 IPC 读取的字节上限（8 MiB）。超出只截断，不报错。 */
export const MAX_READ_LENGTH = 8 * 1024 * 1024

export type ResolveMediaPathResult = { ok: true; path: string } | { ok: false; error: string }
export type ParseReadRequestResult = { ok: true; offset: number; length: number } | { ok: false; error: string }

/** 仅 Windows 需要大小写折叠；POSIX 路径大小写敏感，折叠会放宽校验。 */
const CASE_INSENSITIVE_FS = process.platform === 'win32'

function caseFold(p: string): string {
  return CASE_INSENSITIVE_FS ? p.toLowerCase() : p
}

/** 去掉尾部分隔符，但保留文件系统根（"D:\\" / "/"）本身。 */
function stripTrailingSeparators(p: string): string {
  let out = p
  const fsRoot = path.parse(out).root
  while (out.length > fsRoot.length && (out.endsWith(path.sep) || out.endsWith('/'))) {
    out = out.slice(0, -1)
  }
  return out
}

/**
 * 规范化白名单根：path.resolve 成绝对路径 + 去掉尾部分隔符。
 * 传入非法值（非字符串 / 空串）返回 ''，调用方应跳过该根。
 */
export function normalizeRoot(raw: string): string {
  if (typeof raw !== 'string') return ''
  const trimmed = raw.trim()
  if (!trimmed) return ''
  try {
    return stripTrailingSeparators(path.resolve(trimmed))
  } catch {
    return ''
  }
}

/** 是否含上级目录引用（".." 片段）。必须在 path.resolve 之前判断——resolve 会把它折叠掉。 */
function hasTraversalSegment(raw: string): boolean {
  return raw.split(/[\\/]+/).some((seg) => seg === '..')
}

function isAllowedMediaExtension(p: string): boolean {
  const ext = path.extname(p).toLowerCase()
  return (ALLOWED_MEDIA_EXTENSIONS as readonly string[]).includes(ext)
}

/** realpath：解析 junction / symlink。失败（文件被删等）返回 null，由调用方降级处理。 */
function tryRealPath(p: string): string | null {
  try {
    let real = fs.realpathSync(p)
    // Windows 长路径会带 \\?\ 前缀（UNC 为 \\?\UNC\），比较前剥掉，
    // 否则前缀不一致会把合法路径误判成越界。
    if (CASE_INSENSITIVE_FS && real.startsWith('\\?\\')) {
      real = real.slice(4)
      if (real.toUpperCase().startsWith('UNC\\')) {
        real = '\\\\' + real.slice(4)
      }
    }
    return real
  } catch {
    return null
  }
}

/** 把白名单根展开成用于前缀比较的集合（字面根 + 其 realpath 根）。 */
function buildAllowedPrefixes(roots: string[]): string[] {
  const prefixes = new Set<string>()
  const list = Array.isArray(roots) ? roots : []
  for (const raw of list) {
    const root = normalizeRoot(raw)
    if (!root) continue
    prefixes.add(caseFold(root))
    const real = tryRealPath(root)
    if (real) prefixes.add(caseFold(stripTrailingSeparators(real)))
  }
  return Array.from(prefixes)
}

/** 目标路径是否位于某个根之下：补分隔符做边界匹配，避免 "D:\a-b" 命中 "D:\a-bc"。 */
function isWithinAnyPrefix(target: string, prefixes: string[]): boolean {
  const folded = caseFold(target)
  return prefixes.some((prefix) => {
    const boundary = prefix.endsWith(path.sep) || prefix.endsWith('/') ? prefix : prefix + path.sep
    return folded === prefix || folded.startsWith(caseFold(boundary))
  })
}

/**
 * 校验一个待读取的媒体文件路径。
 *
 * 规则（任一不满足即 { ok: false, error }）：
 * 1. 非空字符串；
 * 2. path.resolve 后为绝对路径，且不含 ".." 越界片段；
 * 3. 扩展名 ∈ ALLOWED_MEDIA_EXTENSIONS（大小写不敏感）；
 * 4. 真实存在且是普通文件；
 * 5. 位于某个白名单根之下（Windows 大小写不敏感 + 分隔符边界匹配 + realpath 复核）。
 */
export function resolveAllowedMediaPath(rawPath: string, roots: string[]): ResolveMediaPathResult {
  try {
    if (typeof rawPath !== 'string' || !rawPath.trim()) {
      return { ok: false, error: '路径为空' }
    }
    const trimmed = rawPath.trim()
    if (hasTraversalSegment(trimmed)) {
      return { ok: false, error: '路径包含上级目录引用（..）' }
    }
    const resolved = path.resolve(trimmed)
    if (!path.isAbsolute(resolved)) {
      return { ok: false, error: '路径不是绝对路径' }
    }
    if (!isAllowedMediaExtension(resolved)) {
      const ext = path.extname(resolved)
      return { ok: false, error: `不支持的媒体文件扩展名: ${ext || '(无)'}` }
    }
    let stat: fs.Stats
    try {
      stat = fs.statSync(resolved)
    } catch {
      return { ok: false, error: `文件不存在或不可访问: ${resolved}` }
    }
    if (!stat.isFile()) {
      return { ok: false, error: `目标不是普通文件: ${resolved}` }
    }
    const prefixes = buildAllowedPrefixes(roots)
    if (prefixes.length === 0) {
      return { ok: false, error: '没有可用的白名单根目录' }
    }
    if (!isWithinAnyPrefix(resolved, prefixes)) {
      return { ok: false, error: `路径不在允许的根目录之下: ${resolved}` }
    }
    const real = tryRealPath(resolved)
    if (real && !isWithinAnyPrefix(real, prefixes)) {
      return { ok: false, error: `路径真实位置不在允许的根目录之下: ${real}` }
    }
    return { ok: true, path: resolved }
  } catch (e) {
    return { ok: false, error: `路径校验失败: ${e instanceof Error ? e.message : String(e)}` }
  }
}

/** 把任意值收敛成非负整数；undefined / null 返回 fallback，非法值返回 null。 */
function toNonNegativeInt(value: unknown, fallback: number): number | null {
  if (value === undefined || value === null) return fallback
  if (typeof value !== 'number' || !Number.isFinite(value) || !Number.isInteger(value) || value < 0) {
    return null
  }
  return value
}

/**
 * 校验读取请求的 offset / length。
 *
 * - offset / length 必须是有限非负整数，否则 { ok: false }；
 * - 两者缺省分别为 0 与上限；
 * - length 超出上限时截断到上限（**不报错**）；上限自身被硬约束在 MAX_READ_LENGTH 之内。
 */
export function parseReadRequest(
  args: { offset?: unknown; length?: unknown } | null | undefined,
  maxLength: number = MAX_READ_LENGTH,
): ParseReadRequestResult {
  try {
    const requestedCap = toNonNegativeInt(maxLength, MAX_READ_LENGTH)
    const cap = Math.min(requestedCap === null || requestedCap === 0 ? MAX_READ_LENGTH : requestedCap, MAX_READ_LENGTH)
    const source = args && typeof args === 'object' ? args : {}
    const offset = toNonNegativeInt(source.offset, 0)
    if (offset === null) {
      return { ok: false, error: 'offset 必须是非负整数' }
    }
    const length = toNonNegativeInt(source.length, cap)
    if (length === null) {
      return { ok: false, error: 'length 必须是非负整数' }
    }
    return { ok: true, offset, length: Math.min(length, cap) }
  } catch (e) {
    return { ok: false, error: `读取请求解析失败: ${e instanceof Error ? e.message : String(e)}` }
  }
}
