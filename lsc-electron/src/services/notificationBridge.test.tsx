import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  islandManager,
  islandMessageApi,
  installIslandMessageBridge,
} from './notificationBridge'
import { message as antdStaticMessage, App } from 'antd'
import React from 'react'
import { render } from '@testing-library/react'

describe('Island Notification Bridge', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    islandManager.clearImmediately()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('subscribes and receives open toast', () => {
    let received: any = null
    const unsub = islandManager.subscribe((item) => {
      received = item
    })

    islandMessageApi.success('操作成功')
    expect(received).not.toBeNull()
    expect(received?.type).toBe('success')
    expect(received?.content).toBe('操作成功')
    expect(received?.phase).toBe('entering')

    // Fast-forward 60ms to active phase
    vi.advanceTimersByTime(60)
    expect(received?.phase).toBe('active')

    // Fast-forward through duration (default 2800ms)
    vi.advanceTimersByTime(2800)
    expect(received?.phase).toBe('exiting')

    // Fast-forward through exit transition (240ms)
    vi.advanceTimersByTime(240)
    expect(received).toBeNull()

    unsub()
  })

  it('handles duration: 0 as sticky notification', () => {
    let received: any = null
    const unsub = islandManager.subscribe((item) => {
      received = item
    })

    islandMessageApi.error({ content: '磁盘空间不足', duration: 0 })
    expect(received?.duration).toBe(0)

    // Advance lots of time, still active
    vi.advanceTimersByTime(100000)
    expect(received?.phase).toBe('active')

    // Manually dismiss
    islandManager.dismiss()
    expect(received?.phase).toBe('exiting')
    vi.advanceTimersByTime(240)
    expect(received).toBeNull()

    unsub()
  })

  it('updates in-place when matching key arrives (e.g. loading to success)', () => {
    let received: any = null
    const unsub = islandManager.subscribe((item) => {
      received = item
    })

    islandMessageApi.loading({ content: '采集音频中...', key: 'align', duration: 0 })
    expect(received?.type).toBe('loading')
    expect(received?.content).toBe('采集音频中...')
    expect(received?.key).toBe('align')

    // Update with success
    islandMessageApi.success({ content: '对齐完成！', key: 'align', duration: 2 })
    expect(received?.type).toBe('success')
    expect(received?.content).toBe('对齐完成！')
    expect(received?.duration).toBe(2000)

    unsub()
  })

  it('supports destroy(key) explicitly', () => {
    let received: any = null
    const unsub = islandManager.subscribe((item) => {
      received = item
    })

    islandMessageApi.loading({ content: '长任务...', key: 'task-1' })
    expect(received?.key).toBe('task-1')

    islandMessageApi.destroy('task-1')
    expect(received?.phase).toBe('exiting')
    vi.advanceTimersByTime(240)
    expect(received).toBeNull()

    unsub()
  })

  it('aggregates duplicate messages in short interval', () => {
    let received: any = null
    const unsub = islandManager.subscribe((item) => {
      received = item
    })

    islandMessageApi.info('切片已添加')
    expect(received?.count).toBe(1)

    islandMessageApi.info('切片已添加')
    expect(received?.count).toBe(2)

    islandMessageApi.info('切片已添加')
    expect(received?.count).toBe(3)

    unsub()
  })

  it('pauses on mouse enter and resumes on mouse leave', () => {
    let received: any = null
    const unsub = islandManager.subscribe((item) => {
      received = item
    })

    islandMessageApi.success('自动保存成功', 2) // 2000ms
    vi.advanceTimersByTime(1000) // 1000ms left
    expect(received?.phase).toBe('active')

    islandManager.pause()
    vi.advanceTimersByTime(5000) // Should not dismiss
    expect(received?.phase).toBe('active')

    islandManager.resume()
    vi.advanceTimersByTime(950)
    expect(received?.phase).toBe('active')
    vi.advanceTimersByTime(100)
    expect(received?.phase).toBe('exiting')

    unsub()
  })

  it('calls onClose callback when dismissed', () => {
    const onClose = vi.fn()
    islandMessageApi.success('通知', 1, onClose)
    vi.advanceTimersByTime(1000)
    expect(islandManager.getCurrent()?.phase).toBe('exiting')
    vi.advanceTimersByTime(240)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('integrates with antd static message and App.useApp after install', () => {
    installIslandMessageBridge()

    let received: any = null
    const unsub = islandManager.subscribe((item) => {
      received = item
    })

    // Static call
    antdStaticMessage.warning('警告信息')
    expect(received?.type).toBe('warning')
    expect(received?.content).toBe('警告信息')

    // Dismiss static call
    islandManager.clearImmediately()

    // Context call
    function Dummy() {
      const { message } = App.useApp()
      React.useEffect(() => {
        message.info('来自 hook 的通知')
      }, [message])
      return <div>dummy</div>
    }

    render(
      <App>
        <Dummy />
      </App>
    )

    expect(received?.content).toBe('来自 hook 的通知')
    unsub()
  })
})
