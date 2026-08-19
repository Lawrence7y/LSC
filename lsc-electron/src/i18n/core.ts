/**
 * LSC 轻量 i18n 核心。
 *
 * 设计约定：
 * - 词典 key 使用「中文原文」，英文翻译放在 en-US 词典中；
 *   未命中时回退原文（中文），保证任何遗漏都不会崩溃。
 * - 中文（zh-CN）为恒等映射，无需维护 zh 词典。
 * - `t()` 为纯函数，可在任何模块（组件外）使用；组件内配合 `useI18n()`
 *   订阅语言变化触发重渲染。
 * - 语言偏好持久化到 localStorage（key: lsc.locale），
 *   默认跟随系统语言（非中文环境默认英文）。
 */
import { useSyncExternalStore } from 'react'
import { enDict } from './en'

export type Locale = 'zh-CN' | 'en-US'

const STORAGE_KEY = 'lsc.locale'

export const LOCALES: { value: Locale; label: string }[] = [
  { value: 'zh-CN', label: '简体中文' },
  { value: 'en-US', label: 'English' },
]

function detectInitialLocale(): Locale {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'zh-CN' || saved === 'en-US') return saved
  } catch {
    // localStorage 不可用时忽略，走系统语言检测
  }
  const lang = typeof navigator !== 'undefined' ? navigator.language : ''
  return lang.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en-US'
}

let currentLocale: Locale = detectInitialLocale()

const listeners = new Set<() => void>()

export function getLocale(): Locale {
  return currentLocale
}

export function setLocale(locale: Locale): void {
  if (locale === currentLocale) return
  currentLocale = locale
  try {
    localStorage.setItem(STORAGE_KEY, locale)
  } catch {
    // 忽略持久化失败
  }
  try {
    document.documentElement.lang = locale === 'zh-CN' ? 'zh-CN' : 'en'
  } catch {
    // 非浏览器环境忽略
  }
  listeners.forEach((l) => l())
}

export function subscribeLocale(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** 插值：{name} 占位符替换。 */
function interpolate(text: string, params?: Record<string, string | number>): string {
  if (!params) return text
  let out = text
  for (const [k, v] of Object.entries(params)) {
    out = out.split(`{${k}}`).join(String(v))
  }
  return out
}

/**
 * 后端/主进程动态拼接的错误文案无法用精确 key 命中，这里按前缀/片段做结构化翻译：
 * - "发生错误：{raw}" → "Error: {raw}"
 * - "{msg}（原始错误：{snippet}）" → "{msg} (raw: {snippet})"
 * - "GitHub API 返回异常状态码: {code}" → "GitHub API returned unexpected status code: {code}"
 * - "网络请求失败: {msg}" → "Network request failed: {msg}"
 */
function translateDynamicError(key: string): string {
  if (key.startsWith('发生错误：')) {
    return `Error: ${key.slice('发生错误：'.length)}`
  }
  const marker = '（原始错误：'
  const idx = key.indexOf(marker)
  if (idx >= 0) {
    const head = key.slice(0, idx)
    let tail = key.slice(idx + marker.length)
    if (tail.endsWith('）')) tail = tail.slice(0, -1)
    const headEn = enDict[head] ?? head
    return `${headEn} (raw: ${tail})`
  }
  const ghPrefix = 'GitHub API 返回异常状态码: '
  if (key.startsWith(ghPrefix)) {
    return `GitHub API returned unexpected status code: ${key.slice(ghPrefix.length)}`
  }
  const netPrefix = '网络请求失败: '
  if (key.startsWith(netPrefix)) {
    return `Network request failed: ${key.slice(netPrefix.length)}`
  }
  return enDict[key] ?? key
}

/**
 * 翻译函数：key 为中文原文；en-US 下查词典，zh-CN 或未命中时返回原文。
 * 可在任意模块调用（组件外亦可），语言切换后下一次调用即生效。
 */
export function t(key: string, params?: Record<string, string | number>): string {
  let text = key
  if (currentLocale === 'en-US') {
    text = translateDynamicError(key)
  }
  return interpolate(text, params)
}

export type I18nT = typeof t

/**
 * React Hook：订阅语言变化，语言切换时触发重渲染。
 * 组件内统一使用 `const { t } = useI18n()`。
 */
export function useI18n(): { t: typeof t; locale: Locale; setLocale: typeof setLocale } {
  const locale = useSyncExternalStore(subscribeLocale, getLocale)
  return { t, locale, setLocale }
}
