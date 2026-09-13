/**
 * 本地媒体文件读取（Electron 主进程 IPC 封装）。
 *
 * 方案 A：回看不再由后端 FFmpeg 转码推流，而是由渲染进程按字节读取本地录制文件，
 * 经 fMP4 切分后直接送 MSE。本模块只负责主进程 IPC 调用与能力探测。
 */

export interface LocalMediaInfo {
  ok: boolean
  size: number
  mtimeMs: number
  error?: string
}

export interface LocalMediaChunk {
  ok: boolean
  data?: Uint8Array
  bytesRead: number
  size: number
  eof: boolean
  error?: string
}

interface LocalMediaBridge {
  info: (payload: { path: string }) => Promise<LocalMediaInfo>
  read: (payload: { path: string; offset: number; length: number }) => Promise<LocalMediaChunk>
  allowRoot: (payload: { root: string }) => Promise<{ ok: boolean; roots: string[] }>
  roots: () => Promise<{ roots: string[] }>
}

function bridge(): LocalMediaBridge | undefined {
  if (typeof window === 'undefined') return undefined
  const api = window.electronAPI as unknown as { localMedia?: LocalMediaBridge } | undefined
  return api?.localMedia
}

/** 主进程是否提供本地媒体读取能力（浏览器预览模式下为 false）。 */
export function isLocalMediaAvailable(): boolean {
  const b = bridge()
  return Boolean(b?.info && b?.read)
}

/** 主进程白名单未覆盖该路径时的错误文案（见 electron/localMedia.ts） */
function isRootError(error?: string): boolean {
  return Boolean(error && error.includes('白名单'))
}

function toBytes(value: unknown): Uint8Array | undefined {
  if (!value) return undefined
  if (value instanceof Uint8Array) return value
  if (value instanceof ArrayBuffer) return new Uint8Array(value)
  // Node Buffer 经结构化克隆后会带 buffer/byteOffset 信息
  const view = value as { buffer?: ArrayBuffer; byteOffset?: number; byteLength?: number }
  if (view.buffer instanceof ArrayBuffer) {
    return new Uint8Array(view.buffer, view.byteOffset ?? 0, view.byteLength ?? view.buffer.byteLength)
  }
  return undefined
}

export async function localMediaInfo(path: string): Promise<LocalMediaInfo> {
  const b = bridge()
  if (!b?.info) return { ok: false, size: 0, mtimeMs: 0, error: 'local media unavailable' }
  try {
    let res = await b.info({ path })
    if (!res?.ok && isRootError(res?.error)) {
      await ensureLocalMediaRoot(path)
      res = await b.info({ path })
    }
    return {
      ok: Boolean(res?.ok),
      size: Number(res?.size) || 0,
      mtimeMs: Number(res?.mtimeMs) || 0,
      error: res?.error,
    }
  } catch (e) {
    return { ok: false, size: 0, mtimeMs: 0, error: String(e) }
  }
}

export async function localMediaRead(path: string, offset: number, length: number): Promise<LocalMediaChunk> {
  const b = bridge()
  if (!b?.read) {
    return { ok: false, bytesRead: 0, size: 0, eof: true, error: 'local media unavailable' }
  }
  try {
    let res = await b.read({ path, offset, length })
    if (!res?.ok && isRootError(res?.error)) {
      await ensureLocalMediaRoot(path)
      res = await b.read({ path, offset, length })
    }
    return {
      ok: Boolean(res?.ok),
      data: toBytes(res?.data),
      bytesRead: Number(res?.bytesRead) || 0,
      size: Number(res?.size) || 0,
      eof: Boolean(res?.eof),
      error: res?.error,
    }
  } catch (e) {
    return { ok: false, bytesRead: 0, size: 0, eof: true, error: String(e) }
  }
}

const _registeredRoots = new Set<string>()

/** 由文件路径推导所在目录（兼容 / 与 \\ 两种分隔符）。 */
export function dirnameOf(filePath: string): string {
  const normalized = filePath.replace(/[\\/]+$/, '')
  const idx = Math.max(normalized.lastIndexOf('/'), normalized.lastIndexOf('\\'))
  return idx > 0 ? normalized.slice(0, idx) : ''
}

/**
 * 确保某个文件所在目录已在主进程白名单内。
 *
 * 主进程启动时会从 settings.json 的 output_dir 装载白名单；全新环境可能还没有
 * settings.json（白名单为空 → 一律读失败）。此处用**后端下发的房间录制路径**所在
 * 目录做一次显式注册，成功后缓存，避免每次读取都往返。
 */
export async function ensureLocalMediaRoot(filePath: string): Promise<void> {
  const dir = dirnameOf(filePath)
  if (!dir || _registeredRoots.has(dir)) return
  const roots = await allowLocalMediaRoot(dir)
  if (roots.length > 0) _registeredRoots.add(dir)
}

/** 供测试重置注册缓存 */
export function resetLocalMediaRootCache(): void {
  _registeredRoots.clear()
}

/** 把录制目录加入主进程白名单（可选；主进程通常已从 settings.json 读取 output_dir）。 */
export async function allowLocalMediaRoot(root: string): Promise<string[]> {
  const b = bridge()
  if (!b?.allowRoot || !root) return []
  try {
    const res = await b.allowRoot({ root })
    return res?.roots ?? []
  } catch {
    return []
  }
}
