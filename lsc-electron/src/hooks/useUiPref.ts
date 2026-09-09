import { useCallback, useRef, useState } from 'react'

const NS = 'lsc.ui.'

function read<T>(fullKey: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(fullKey)
    if (raw === null) return fallback
    return JSON.parse(raw) as T
  } catch {
    // localStorage 不可用 / 值损坏时静默回落到默认值
    return fallback
  }
}

/**
 * 界面偏好的持久化 state（与 useState 同签名，可直接替换）。
 *
 * 为什么要收口：此前持久化各写各的 —— 「完成后生成剪映草稿」手写 localStorage，
 * 而切片面板折叠、房间排序、时间线缩放、播放速率、持续/单次模式全部只活在内存里，
 * 重启即回到默认值。用户每次开应用都要重摆一遍界面。
 *
 * 注意：这里存的是「界面偏好」，不是业务数据；业务配置仍走 save_settings 到后端。
 */
export function useUiPref<T>(
  key: string,
  fallback: T | (() => T),
): [T, (value: T | ((prev: T) => T)) => void] {
  const fullKey = NS + key
  const [value, setValue] = useState<T>(() => {
    let raw: string | null = null
    try { raw = localStorage.getItem(fullKey) } catch { raw = null }
    if (raw !== null) {
      try { return JSON.parse(raw) as T } catch { /* 值损坏时回落到 fallback */ }
    }
    return typeof fallback === 'function' ? (fallback as () => T)() : fallback
  })
  const keyRef = useRef(fullKey)
  keyRef.current = fullKey

  const set = useCallback((next: T | ((prev: T) => T)) => {
    setValue(prev => {
      const resolved = typeof next === 'function'
        ? (next as (p: T) => T)(prev)
        : next
      try {
        localStorage.setItem(keyRef.current, JSON.stringify(resolved))
      } catch {
        // 写入失败（隐私模式 / 配额）不影响本次会话使用
      }
      return resolved
    })
  }, [])

  return [value, set]
}

/** 一次性读取（用于迁移手写的 localStorage 开关到 useUiPref） */
export function readUiPref<T>(key: string, fallback: T): T {
  return read(NS + key, fallback)
}
