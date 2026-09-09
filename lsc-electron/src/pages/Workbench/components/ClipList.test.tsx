import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ClipList, getClipStableId } from './ClipList'
import type { ClipSegment } from '@/types'

// ─── 测试数据 ────────────────────────────────────────────────────────

function makeClip(overrides: Partial<ClipSegment> = {}): ClipSegment {
  return {
    start: 100,
    end: 130,
    label: '回合 1 高光',
    room_id: 'room-1',
    room_name: '主播A',
    clip_id: 'clip-001',
    confirm_status: 'user_confirmed',
    ...overrides,
  }
}

const defaultProps = {
  clips: [] as ClipSegment[],
  onDelete: vi.fn(),
  onExport: vi.fn(),
}

beforeEach(() => {
  vi.clearAllMocks()
  // ClipThumbnail 依赖 electronAPI
  ;(window as any).electronAPI = undefined
})

// ─── getClipStableId ─────────────────────────────────────────────────

describe('getClipStableId', () => {
  it('优先使用 clip_id', () => {
    expect(getClipStableId(makeClip({ clip_id: 'abc', round_key: 'rk' }))).toBe('abc')
  })
  it('无 clip_id 时用 round_key', () => {
    expect(getClipStableId(makeClip({ clip_id: undefined, round_key: 'rk-1' }))).toBe('rk-1')
  })
  it('兜底使用组合键', () => {
    expect(getClipStableId(makeClip({ clip_id: undefined, round_key: undefined }))).toBe('room-1-100-130')
  })
})

// ─── 渲染 ────────────────────────────────────────────────────────────

describe('ClipList 渲染', () => {
  it('空列表显示「暂无切片」', () => {
    render(<ClipList {...defaultProps} />)
    expect(screen.getByText('暂无切片')).toBeTruthy()
  })

  it('渲染切片标签与数量', () => {
    const clips = [makeClip(), makeClip({ clip_id: 'clip-002', label: '回合 2 高光', start: 200, end: 240 })]
    render(<ClipList {...defaultProps} clips={clips} />)
    expect(screen.getByText('回合 1 高光')).toBeTruthy()
    expect(screen.getByText('回合 2 高光')).toBeTruthy()
    // 标题计数
    expect(screen.getByText('· 2')).toBeTruthy()
  })

  it('全部切片直接展示在列表中', () => {
    const clips = [
      makeClip({ confirm_status: 'pending' }),
      makeClip({ clip_id: 'clip-002', confirm_status: 'user_confirmed' }),
    ]
    render(<ClipList {...defaultProps} clips={clips} />)
    expect(screen.getByText('· 2')).toBeTruthy()
  })

  it('持续分析产出的切片显示「持续分析」标签', () => {
    render(<ClipList {...defaultProps} clips={[makeClip({ source: 'ai_highlight' })]} />)
    expect(screen.getByText('持续分析')).toBeTruthy()
  })

  it('手动切片显示「手动切片」标签', () => {
    render(<ClipList {...defaultProps} clips={[makeClip({ source: 'manual' })]} />)
    expect(screen.getByText('手动切片')).toBeTruthy()
  })

  it('时间直接展示，取消「实际/预估」等状态标记', () => {
    render(
      <ClipList
        {...defaultProps}
        clips={[makeClip({ start: 100, end: 130, recording_start_sec: 94, recording_end_sec: 124 })]}
      />,
    )
    const time = document.querySelector('.clip-row-v2__time')
    expect(time?.textContent).toContain('00:01:34→00:02:04')
    expect(document.querySelector('.clip-row-v2__axis')).toBeNull()
  })
})

// ─── 交互：删除 ──────────────────────────────────────────────────────

describe('ClipList 删除交互', () => {
  it('点击删除按钮回调 onDelete 并传入稳定 ID', async () => {
    const onDelete = vi.fn()
    render(<ClipList {...defaultProps} clips={[makeClip()]} onDelete={onDelete} />)
    // 删除按钮带 danger + DeleteOutlined，通过 tooltip title 定位
    const deleteBtn = document.querySelector('.clip-row-v2__acts button[aria-label="delete"], .clip-row-v2__acts .ant-btn-dangerous')

    expect(deleteBtn).toBeTruthy()
    fireEvent.click(deleteBtn!)
    expect(onDelete).toHaveBeenCalledWith('clip-001')
  })
})

// ─── 交互：选择 ──────────────────────────────────────────────────────

describe('ClipList 选择交互', () => {
  it('勾选 checkbox 后回调 onSelectedClipIdsChange', async () => {
    const onSelectedChange = vi.fn()
    render(
      <ClipList
        {...defaultProps}
        clips={[makeClip()]}
        onSelectedClipIdsChange={onSelectedChange}
      />,
    )
    const checkbox = document.querySelector('.clip-row-v2 input[type="checkbox"]') as HTMLInputElement
    expect(checkbox).toBeTruthy()
    // userEvent 完整模拟点击（happy-dom 中 fireEvent 不触发 checkbox 激活行为）
    await userEvent.click(checkbox)
    expect(onSelectedChange).toHaveBeenCalledTimes(1)
    const ids: Set<string> = onSelectedChange.mock.calls[0][0]
    expect(ids.has('clip-001')).toBe(true)
  })

  it('受控模式下外部 selectedClipIds 生效', () => {
    render(
      <ClipList
        {...defaultProps}
        clips={[makeClip()]}
        selectedClipIds={new Set(['clip-001'])}
      />,
    )
    const row = document.querySelector('.clip-row-v2')
    expect(row?.classList.contains('is-sel')).toBe(true)
  })
})

// ─── 交互：导出 ──────────────────────────────────────────────────────

describe('ClipList 导出交互', () => {
  it('切片可直接导出，点击导出按钮回调 onExport', () => {
    const onExport = vi.fn()
    render(<ClipList {...defaultProps} clips={[makeClip()]} onExport={onExport} />)
    const exportBtn = document.querySelector('.clip-row-v2__acts .act-primary')
    expect(exportBtn).toBeTruthy()
    fireEvent.click(exportBtn!)
    expect(onExport).toHaveBeenCalledWith(expect.objectContaining({ clip_id: 'clip-001' }))
  })

  it('未确认边界切片取消待确认状态，导出按钮立即可用', () => {
    const onExport = vi.fn()
    render(<ClipList {...defaultProps} clips={[makeClip({ confirm_status: 'pending' })]} onExport={onExport} />)
    const exportBtn = document.querySelector('.clip-row-v2__acts .act-primary') as HTMLButtonElement | null
    expect(exportBtn).toBeTruthy()
    expect(exportBtn?.disabled).toBe(false)
    fireEvent.click(exportBtn!)
    expect(onExport).toHaveBeenCalled()
  })

  it('导出中状态显示进度', () => {
    const clips = [makeClip({ export_status: 'exporting', job_id: 'job-1' })]
    render(
      <ClipList
        {...defaultProps}
        clips={clips}
        exportProgress={{ 'job-1': { percent: 45, elapsed: 2, total: 5 } }}
      />,
    )
    expect(screen.getByText('45%')).toBeTruthy()
  })
})
