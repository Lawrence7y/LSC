import { describe, expect, it, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import {
  Timeline,
  formatTickTime,
  clamp,
  chooseTickInterval,
  chooseMajorInterval,
  isKeyStage,
  findSnapTarget,
} from './index'

// ─── 纯函数：坐标计算 ───────────────────────────────────────────────

describe('formatTickTime', () => {
  it('秒级刻度', () => {
    expect(formatTickTime(0)).toBe('0s')
    expect(formatTickTime(30)).toBe('30s')
    expect(formatTickTime(59)).toBe('59s')
  })

  it('分钟级刻度', () => {
    expect(formatTickTime(60)).toBe('1m')
    expect(formatTickTime(90)).toBe('1m30s')
    expect(formatTickTime(300)).toBe('5m')
  })

  it('小时级刻度', () => {
    expect(formatTickTime(3600)).toBe('1h')
    expect(formatTickTime(3660)).toBe('1h1m')
    expect(formatTickTime(5400)).toBe('1h30m')
    expect(formatTickTime(7200)).toBe('2h')
  })

  it('负值钳位到 0', () => {
    expect(formatTickTime(-5)).toBe('0s')
  })
})

describe('clamp', () => {
  it('范围内不变', () => {
    expect(clamp(5, 0, 10)).toBe(5)
  })
  it('低于最小值钳位', () => {
    expect(clamp(-3, 0, 10)).toBe(0)
  })
  it('高于最大值钳位', () => {
    expect(clamp(15, 0, 10)).toBe(10)
  })
})

describe('chooseTickInterval', () => {
  it('短时长低缩放选择小间隔', () => {
    // 60s 窗口，800px 宽，目标间距 120px → rawInterval = 60/800*120 = 9 → 选 10
    const interval = chooseTickInterval(60, 1, 120, 800, 800)
    expect(interval).toBe(10)
  })

  it('长时长选择大间隔', () => {
    // 3600s 窗口，800px → rawInterval = 3600/800*120 = 540 → 选 600
    const interval = chooseTickInterval(3600, 1, 120, 800, 800)
    expect(interval).toBe(600)
  })

  it('放大后间隔缩小', () => {
    const zoomedOut = chooseTickInterval(3600, 1, 120, 800, 800)
    const zoomedIn = chooseTickInterval(3600, 4, 120, 3200, 800)
    expect(zoomedIn).toBeLessThan(zoomedOut)
  })

  it('超长时长选择大间隔', () => {
    // 86400s(1天)，800px → rawInterval = 12960 → 选 14400
    const interval = chooseTickInterval(86400, 1, 120, 800, 800)
    expect(interval).toBe(14400)
  })
})

describe('chooseMajorInterval', () => {
  it('次刻度 10s 可见 600s → 主刻度 120s', () => {
    expect(chooseMajorInterval(10, 600)).toBe(120)
  })

  it('次刻度 60s 可见 3600s → 主刻度落在 landmark', () => {
    const major = chooseMajorInterval(60, 3600)
    expect(major % 60).toBe(0)
    expect(major).toBeGreaterThanOrEqual(240)
  })

  it('次刻度 300s → 主刻度至少 1200 或回退 5 倍', () => {
    const major = chooseMajorInterval(300, 7200)
    expect(major).toBeGreaterThanOrEqual(1200)
  })
})

describe('isKeyStage', () => {
  it('整十分钟为关键阶段', () => {
    expect(isKeyStage(600)).toBe(true)
    expect(isKeyStage(1200)).toBe(true)
  })
  it('整小时为关键阶段', () => {
    expect(isKeyStage(3600)).toBe(true)
    expect(isKeyStage(7200)).toBe(true)
  })
  it('非关键点', () => {
    expect(isKeyStage(0)).toBe(false)
    expect(isKeyStage(90)).toBe(false)
    expect(isKeyStage(601)).toBe(false)
  })
})

describe('findSnapTarget（磁吸坐标计算）', () => {
  it('靠近 markIn 时磁吸到 markIn', () => {
    const result = findSnapTarget(10.5, 120, 10, null, 60, 10)
    expect(result).toBe(10)
  })

  it('靠近 markOut 时磁吸到 markOut', () => {
    const result = findSnapTarget(49.6, 120, null, 50, 60, 10)
    expect(result).toBe(50)
  })

  it('靠近播放头时磁吸（优先级低于标记点）', () => {
    const result = findSnapTarget(60.4, 120, null, null, 60, 10)
    expect(result).toBe(60)
  })

  it('skipCurrentTime 跳过播放头磁吸', () => {
    // 65.5 距播放头 65.2 仅 0.3（阈值 0.85 内），距刻度 60/70 均超阈值
    const snapped = findSnapTarget(65.5, 120, null, null, 65.2, 10)
    expect(snapped).toBe(65.2)
    const result = findSnapTarget(65.5, 120, null, null, 65.2, 10, [], { skipCurrentTime: true })
    expect(result).toBe(65.5)
  })

  it('靠近刻度线时磁吸', () => {
    const result = findSnapTarget(30.6, 120, null, null, 90, 10)
    expect(result).toBe(30)
  })

  it('远离所有目标时返回原始值', () => {
    const result = findSnapTarget(25.5, 120, null, null, 90, 10)
    expect(result).toBe(25.5)
  })

  it('高光片段端点磁吸（优先级 90 > 播放头 80）', () => {
    const highlights = [{ id: 'h1', start: 20, end: 30, score: 0.9 }]
    // 距高光 end=30 仅 0.5，距播放头 30.3 仅 0.2，但高光优先级更高
    const result = findSnapTarget(30.5, 120, null, null, 30.3, 10, highlights)
    expect(result).toBe(30)
  })

  it('clip 端点磁吸', () => {
    const clips = [{ start: 40, end: 55 }]
    const result = findSnapTarget(40.3, 120, null, null, 90, 10, [], { clips })
    expect(result).toBe(40)
  })

  it('支持自定义 snapThreshold（精细吸附与禁用）', () => {
    const resultFine = findSnapTarget(30.2, 120, null, null, 90, 10, [], { snapThreshold: 0.05 })
    expect(resultFine).toBe(30.2)

    const resultFineHit = findSnapTarget(30.03, 120, null, null, 90, 10, [], { snapThreshold: 0.05 })
    expect(resultFineHit).toBe(30)

    const resultDisabled = findSnapTarget(30.03, 120, null, null, 90, 10, [], { snapThreshold: 0 })
    expect(resultDisabled).toBe(30.03)
  })
})

// ─── 组件渲染：播放头 / 选区 / 标记坐标 ─────────────────────────────

vi.mock('@/utils/playheadStore', () => ({
  subscribeDisplayPlayhead: vi.fn(() => () => {}),
}))

const baseProps = {
  duration: 120,
  currentTime: 60,
  markIn: null as number | null,
  markOut: null as number | null,
  onSeek: vi.fn(),
  onMarkIn: vi.fn(),
  onMarkOut: vi.fn(),
}

describe('Timeline 组件渲染', () => {
  it('播放头位于 50% 处（currentTime=60 / duration=120）', () => {
    const { container } = render(<Timeline {...baseProps} />)
    const playhead = container.querySelector('.lsc-timeline__playhead') as HTMLElement
    expect(playhead).toBeTruthy()
    expect(playhead.style.left).toBe('50%')
  })

  it('入出点标记渲染在正确百分比位置', () => {
    const { container } = render(
      <Timeline {...baseProps} markIn={30} markOut={90} />,
    )
    const markerIn = container.querySelector('.lsc-timeline__marker--in') as HTMLElement
    const markerOut = container.querySelector('.lsc-timeline__marker--out') as HTMLElement
    expect(markerIn.style.left).toBe('25%')
    expect(markerOut.style.left).toBe('75%')
  })

  it('选区宽度和位置正确（markIn=30, markOut=90 → left 25%, width 50%）', () => {
    const { container } = render(
      <Timeline {...baseProps} markIn={30} markOut={90} />,
    )
    const selection = container.querySelector('.lsc-timeline__selection') as HTMLElement
    expect(selection).toBeTruthy()
    expect(selection.style.left).toBe('25%')
    expect(selection.style.width).toBe('50%')
  })

  it('无入出点时不渲染选区', () => {
    const { container } = render(<Timeline {...baseProps} />)
    expect(container.querySelector('.lsc-timeline__selection')).toBeNull()
  })

  it('高光色带坐标正确（start=30, end=60 → left 25%, width 25%）', () => {
    const highlights = [{ id: 'h1', start: 30, end: 60, score: 0.8 }]
    const { container } = render(<Timeline {...baseProps} highlights={highlights} />)
    const band = container.querySelector('.lsc-timeline__highlight') as HTMLElement
    expect(band).toBeTruthy()
    expect(band.style.left).toBe('25%')
    expect(band.style.width).toBe('25%')
  })

  it('clips 色块坐标正确', () => {
    const clips = [{ start: 0, end: 30 }]
    const { container } = render(<Timeline {...baseProps} clips={clips} />)
    const clip = container.querySelector('.lsc-timeline__clip') as HTMLElement
    expect(clip).toBeTruthy()
    expect(clip.style.left).toBe('0%')
    expect(clip.style.width).toBe('25%')
  })

  it('缓冲进度条宽度正确（buffered=90 → 75%）', () => {
    const { container } = render(<Timeline {...baseProps} buffered={90} />)
    const buffered = container.querySelector('.lsc-timeline__buffered') as HTMLElement
    expect(buffered.style.width).toBe('75%')
  })

  it('duration=0 时不崩溃（effectiveDuration 最小为 1）', () => {
    const { container } = render(
      <Timeline {...baseProps} duration={0} currentTime={0} />,
    )
    expect(container.querySelector('.lsc-timeline')).toBeTruthy()
  })
})

// ─── 时间线点击语义 ────────────────────────────────────────────

describe('Timeline 点击语义：只有 scrub，没有隐藏修饰键', () => {
  /**
   * 回归目标：旧实现把 Shift+点击 / Ctrl+点击当作「标入点 / 标出点」，
   * 但 onMarkIn/onMarkOut 读的是播放头时间而非点击位置——在 100s 处点下去
   * 实际标在播放头（例如 500s），而且该修饰键在任何提示/文档里都不存在。
   */
  const makeProps = () => ({
    ...baseProps,
    onMarkIn: vi.fn(),
    onMarkOut: vi.fn(),
    onScrubStart: vi.fn(),
  })

  const scrollOf = (container: HTMLElement) =>
    container.querySelector('.lsc-timeline__scroll') as Element

  it.each([
    ['普通点击', {}],
    ['Shift+点击', { shiftKey: true }],
    ['Ctrl+点击', { ctrlKey: true }],
  ])('%s 不会打标，而是开始 scrub', (_label, init) => {
    const props = makeProps()
    const { container, unmount } = render(<Timeline {...props} />)
    fireEvent.mouseDown(scrollOf(container), { clientX: 100, ...init })

    expect(props.onMarkIn).not.toHaveBeenCalled()
    expect(props.onMarkOut).not.toHaveBeenCalled()
    expect(props.onScrubStart).toHaveBeenCalledTimes(1)

    // 结束拖拽，避免 window 监听泄到下一个用例
    fireEvent.mouseUp(window)
    unmount()
  })

  it('入出点标记提示了右键删除（原本只能靠口头传播）', () => {
    const { container } = render(<Timeline {...makeProps()} markIn={30} markOut={90} />)
    const markerIn = container.querySelector('.lsc-timeline__marker--in') as HTMLElement
    const markerOut = container.querySelector('.lsc-timeline__marker--out') as HTMLElement
    expect(markerIn.getAttribute('title')).toContain('右键删除')
    expect(markerOut.getAttribute('title')).toContain('右键删除')
  })

  it('时间线按下与拖动时触发 onScrubMove 供画面丝滑预览', () => {
    const onScrubMove = vi.fn()
    const props = {
      ...baseProps,
      windowStart: 100,
      duration: 120,
      onScrubMove,
    }
    const { container, unmount } = render(<Timeline {...props} />)
    const scroll = scrollOf(container)
    // 模拟 getBoundingClientRect
    vi.spyOn(container.querySelector('.lsc-timeline__track') as HTMLElement, 'getBoundingClientRect').mockReturnValue({
      left: 0,
      top: 0,
      width: 1000,
      height: 60,
      right: 1000,
      bottom: 60,
      x: 0,
      y: 0,
      toJSON: () => {},
    })

    fireEvent.mouseDown(scroll, { clientX: 500 })
    expect(onScrubMove).toHaveBeenCalledTimes(1)
    // 500px / 1000px = 50% * 120s = 60s + ws(100s) = 160s
    expect(onScrubMove).toHaveBeenCalledWith(160)

    fireEvent.mouseUp(window)
    unmount()
  })
})

// ─── 回放标签如实口径（2026-09-15 真机排查：标签必须说实话） ─────────

describe('即时回放起点标签', () => {
  const dvrProps = (over?: Record<string, unknown>) => ({
    ...baseProps,
    duration: 300,
    windowStart: 0,
    ...over,
  })

  it('标签显示真实起点与实际可回放时长（按可见跨度取小），紫标落在真实缓冲起点', () => {
    const { container } = render(
      <Timeline
        {...dvrProps({
          dvrStart: 200,
          dvrReplay: {
            start: 200,
            configuredStart: 100,
            availableSeconds: 130,
            configuredSeconds: 300,
            effectiveSeconds: 300,
            degraded: false,
          },
        })}
      />,
    )
    const marker = container.querySelector('.lsc-timeline__record-end') as HTMLElement
    expect(marker).toBeTruthy()
    // 300s 轴上 200s → 66.67%
    expect(parseFloat(marker.style.left)).toBeCloseTo(200 / 300 * 100, 3)
    const label = container.querySelector('.lsc-timeline__record-end-label') as HTMLElement
    // 缓冲 130s 但右沿只到 300s ⇒ 可见可回放 = 100s；数字必须与眼睛量到的一致
    expect(label.textContent).toContain('可回放')
    expect(label.textContent).toContain('00:01:40')
  })

  it('缓冲完全落在可见窗口内时如实报出缓冲深度', () => {
    const { container } = render(
      <Timeline
        {...dvrProps({
          duration: 600,
          dvrStart: 200,
          dvrReplay: {
            start: 200,
            configuredStart: 100,
            availableSeconds: 130,
            configuredSeconds: 300,
            effectiveSeconds: 300,
            degraded: false,
          },
        })}
      />,
    )
    const label = container.querySelector('.lsc-timeline__record-end-label') as HTMLElement
    expect(label.textContent).toContain('00:02:10')
  })

  it('设置窗口与真实起点差 > 5s 时画出参考虚线，差得少则不画', () => {
    const far = render(
      <Timeline
        {...dvrProps({
          dvrStart: 200,
          dvrReplay: {
            start: 200,
            configuredStart: 100,
            availableSeconds: 130,
            configuredSeconds: 300,
            effectiveSeconds: 300,
            degraded: false,
          },
        })}
      />,
    )
    expect(far.container.querySelector('.lsc-timeline__replay-configured')).toBeTruthy()
    far.unmount()

    const close = render(
      <Timeline
        {...dvrProps({
          dvrStart: 200,
          dvrReplay: {
            start: 200,
            configuredStart: 197,
            availableSeconds: 300,
            configuredSeconds: 300,
            effectiveSeconds: 300,
            degraded: false,
          },
        })}
      />,
    )
    expect(close.container.querySelector('.lsc-timeline__replay-configured')).toBeNull()
    close.unmount()
  })

  it('标记靠近右沿时标签向左展开（防文字被轨道裁切）', () => {
    const { container } = render(
      <Timeline
        {...dvrProps({
          dvrStart: 240, // 80% > 70% 阈值
          dvrReplay: {
            start: 240,
            configuredStart: null,
            availableSeconds: 60,
            configuredSeconds: 0,
            effectiveSeconds: 30,
            degraded: false,
          },
        })}
      />,
    )
    expect(container.querySelector('.lsc-timeline__record-end-label--flip')).toBeTruthy()
  })

  it('配额缩容时标签点明原因（内存压力）', () => {
    const { container } = render(
      <Timeline
        {...dvrProps({
          dvrStart: 150,
          dvrReplay: {
            start: 150,
            configuredStart: 100,
            availableSeconds: 108,
            configuredSeconds: 300,
            effectiveSeconds: 180,
            degraded: true,
          },
        })}
      />,
    )
    const label = container.querySelector('.lsc-timeline__record-end-label') as HTMLElement
    expect(label.textContent).toContain('内存压力')
    expect(label.textContent).toContain('00:03:00')
  })
})
