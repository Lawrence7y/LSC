/**
 * 静音同步抑制守卫。
 *
 * 背景：程序内部临时改 video.muted（如对齐音频采集期间临时取消静音）时，
 * 会触发 VideoPreview 的 volumechange 监听，把程序内覆盖误同步为用户的
 * 静音偏好（store.preview_muted）。通过该标志在抑制窗口内跳过同步。
 *
 * 集中管理标志名，替代原先跨模块硬编码的 `__lscSuppressMuteSync` 魔法属性，
 * 避免隐式契约漂移（改名一处漏改即静音状态错乱）。
 */
const FLAG = '__lscSuppressMuteSync'

type FlaggedVideo = HTMLVideoElement & { [FLAG]?: boolean }

/** VideoPreview 的 volumechange 处理器在同步 store 前调用，true 时跳过本次同步。 */
export function isMuteSyncSuppressed(video: HTMLVideoElement): boolean {
  return (video as FlaggedVideo)[FLAG] === true
}

/** 在抑制窗口内同步执行 fn；fn 内对 muted 的修改不会触发 store 同步。 */
export function withMuteSyncSuppressed<T>(video: HTMLVideoElement, fn: () => T): T {
  const v = video as FlaggedVideo
  v[FLAG] = true
  try {
    return fn()
  } finally {
    v[FLAG] = false
  }
}
