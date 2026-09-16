import { useCallback, useEffect, useRef, useState } from 'react'

type GetVideo = () => HTMLVideoElement | null | undefined

export type PictureInPictureController = {
  /** 运行环境是否支持原生画中画（Electron/Chromium 支持；不支持时按钮置灰）。 */
  supported: boolean
  /** **本房**当前是否就在小窗里（其他房间的小窗不算）。 */
  active: boolean
  /** 进入 / 退出小窗播放；返回值表示是否切换成功（失败由调用方给用户提示）。 */
  toggle: () => Promise<boolean>
}

function detectSupport(): boolean {
  if (typeof document === 'undefined') return false
  if (document.pictureInPictureEnabled !== true) return false
  return typeof HTMLVideoElement.prototype.requestPictureInPicture === 'function'
}

/**
 * 「缩小为窗口播放」= 浏览器/Electron 的**原生画中画**（Picture-in-Picture）。
 *
 * 为什么不自己做一个悬浮窗口：原生小窗由渲染进程之外合成，能跨应用/跨显示器常驻、
 * 不占工作台布局、窗口关闭按钮/尺寸由系统提供，且不需要我们把 MSE 分片再喂一份。
 *
 * 两处容易踩的坑：
 * 1. 画中画是 **document 级单例**（同一时刻只有一个元素能进小窗），所以状态必须按
 *    `document.pictureInPictureElement === 本房 video` 判定，并监听 document 上的
 *    `enter/leavepictureinpicture`——绑定在 video 上会在播放器重建（换通道/重连）后失效，
 *    而监听 document 的事件是冒泡上来的，天然跟着当前元素走。
 * 2. `requestPictureInPicture()` 需要用户手势，且视频必须已有可解码画面（无数据时抛
 *    InvalidStateError），因此失败必须回报调用方而不是静默吞掉。
 */
export function usePictureInPicture(getVideo: GetVideo): PictureInPictureController {
  const [supported, setSupported] = useState(false)
  const [active, setActive] = useState(false)
  // getVideo 通常是内联箭头函数：用 ref 固定，避免每次渲染重绑 document 监听
  const getVideoRef = useRef(getVideo)
  getVideoRef.current = getVideo

  useEffect(() => {
    setSupported(detectSupport())
  }, [])

  useEffect(() => {
    const sync = () => {
      const video = getVideoRef.current()
      setActive(Boolean(video) && document.pictureInPictureElement === video)
    }
    document.addEventListener('enterpictureinpicture', sync)
    document.addEventListener('leavepictureinpicture', sync)
    sync()
    return () => {
      document.removeEventListener('enterpictureinpicture', sync)
      document.removeEventListener('leavepictureinpicture', sync)
    }
  }, [])

  // 卡片卸载（删房/关预览）时收口：本房还在小窗里就退出来，避免留下无人管理的浮窗
  useEffect(() => () => {
    const video = getVideoRef.current()
    if (video && document.pictureInPictureElement === video) {
      void document.exitPictureInPicture?.().catch(() => {})
    }
  }, [])

  const toggle = useCallback(async () => {
    if (typeof document === 'undefined') return false
    const video = getVideoRef.current()
    if (!video) return false
    try {
      if (document.pictureInPictureElement === video) {
        await document.exitPictureInPicture?.()
      } else {
        // 同一时刻只允许一个小窗：先让别的房间退出，再进本房
        if (document.pictureInPictureElement) {
          await document.exitPictureInPicture?.()
        }
        await video.requestPictureInPicture()
      }
      setActive(document.pictureInPictureElement === video)
      return true
    } catch {
      setActive(document.pictureInPictureElement === video)
      return false
    }
  }, [])

  return { supported, active, toggle }
}
