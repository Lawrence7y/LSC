import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { usePictureInPicture } from './usePictureInPicture'

/**
 * 「缩小为窗口播放」= 原生画中画。这里用双桩替身把三件事钉住：
 * 1. 支持性探测（环境没有 pictureInPictureEnabled 时按钮必须置灰，不能点击后静默失败）；
 * 2. 单例语义：状态按「小窗元素 === 本房 video」判定，别的房间进小窗不影响本房；
 * 3. 失败必须回报调用方（用户手势/无画面数据都会 reject），并且卡片卸载要收口。
 */
describe('usePictureInPicture', () => {
  let pipElement: HTMLVideoElement | null = null
  const exitSpy = vi.fn(async () => {
    const prev = pipElement
    pipElement = null
    document.dispatchEvent(new Event('leavepictureinpicture'))
    return prev as unknown as HTMLVideoElement
  })

  function makeVideo(onRequest?: () => void) {
    const video = document.createElement('video')
    // happy-dom 原型上的方法默认不可写，实例级替换必须用 defineProperty
    Object.defineProperty(video, 'requestPictureInPicture', {
      configurable: true,
      writable: true,
      value: vi.fn(async () => {
        pipElement = video
        document.dispatchEvent(new Event('enterpictureinpicture'))
        onRequest?.()
        return {} as unknown
      }),
    })
    return video
  }

  beforeEach(() => {
    pipElement = null
    exitSpy.mockClear()
    Object.defineProperty(document, 'pictureInPictureEnabled', { configurable: true, value: true })
    Object.defineProperty(document, 'pictureInPictureElement', {
      configurable: true,
      get: () => pipElement,
    })
    Object.defineProperty(document, 'exitPictureInPicture', { configurable: true, value: exitSpy })
    Object.defineProperty(HTMLVideoElement.prototype, 'requestPictureInPicture', {
      configurable: true,
      writable: true,
      value: () => Promise.resolve({}),
    })
  })

  afterEach(() => {
    Object.defineProperty(document, 'pictureInPictureEnabled', { configurable: true, value: undefined })
  })

  it('环境支持画中画时 supported 为真', () => {
    const video = makeVideo()
    const { result } = renderHook(() => usePictureInPicture(() => video))
    expect(result.current.supported).toBe(true)
    expect(result.current.active).toBe(false)
  })

  it('环境不支持（pictureInPictureEnabled 未定义）时 supported 为假', () => {
    Object.defineProperty(document, 'pictureInPictureEnabled', { configurable: true, value: undefined })
    const video = makeVideo()
    const { result } = renderHook(() => usePictureInPicture(() => video))
    expect(result.current.supported).toBe(false)
  })

  it('toggle 进入小窗后 active 为真，再次 toggle 退出', async () => {
    const video = makeVideo()
    const { result } = renderHook(() => usePictureInPicture(() => video))

    await act(async () => { expect(await result.current.toggle()).toBe(true) })
    expect(pipElement).toBe(video)
    expect(result.current.active).toBe(true)

    await act(async () => { expect(await result.current.toggle()).toBe(true) })
    expect(pipElement).toBeNull()
    expect(result.current.active).toBe(false)
    expect(exitSpy).toHaveBeenCalled()
  })

  it('别的房间在小窗里时本房 active 为假；toggle 会先让别的房间退出', async () => {
    const other = makeVideo()
    const mine = makeVideo()
    pipElement = other
    const { result } = renderHook(() => usePictureInPicture(() => mine))
    expect(result.current.active).toBe(false)

    await act(async () => { expect(await result.current.toggle()).toBe(true) })
    expect(exitSpy).toHaveBeenCalledTimes(1)
    expect(pipElement).toBe(mine)
    expect(result.current.active).toBe(true)
  })

  it('requestPictureInPicture 失败时回报 false，且不误报 active', async () => {
    const video = document.createElement('video')
    Object.defineProperty(video, 'requestPictureInPicture', {
      configurable: true,
      writable: true,
      value: vi.fn(async () => { throw new Error('InvalidStateError: no video data') }),
    })
    // 原型上的支持性探测仍为真（否则按钮是禁用态，走不到这里）
    const { result } = renderHook(() => usePictureInPicture(() => video))
    await act(async () => { expect(await result.current.toggle()).toBe(false) })
    expect(result.current.active).toBe(false)
  })

  it('没有 video 元素时 toggle 直接返回 false', async () => {
    const { result } = renderHook(() => usePictureInPicture(() => null))
    await act(async () => { expect(await result.current.toggle()).toBe(false) })
  })

  it('卸载时若本房还在小窗里则退出（不留无人管理的浮窗）', async () => {
    const video = makeVideo()
    const { result, unmount } = renderHook(() => usePictureInPicture(() => video))
    await act(async () => { await result.current.toggle() })
    expect(pipElement).toBe(video)
    unmount()
    expect(exitSpy).toHaveBeenCalled()
  })
})
