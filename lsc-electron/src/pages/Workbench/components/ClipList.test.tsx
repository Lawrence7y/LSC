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

  it('待确认切片显示「待调」计数', () => {
    const clips = [
      makeClip({ confirm_status: 'pending' }),
      makeClip({ clip_id: 'clip-002', confirm_status: 'user_confirmed' }),
    ]
    render(<ClipList {...defaultProps} clips={clips} />)
    expect(screen.getByText('待调 1')).toBeTruthy()
  })

  it('audio_pending 切片显示「OCR 复核中」标签', () => {
    render(<ClipList {...defaultProps} clips={[makeClip({ confirm_status: 'audio_pending' })]} />)
    expect(screen.getByText('OCR 复核中')).toBeTruthy()
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

// ─── 交互：筛选与搜索 ────────────────────────────────────────────────

describe('ClipList 筛选与搜索', () => {
  const clips = [
    makeClip({ clip_id: 'c1', label: '精彩击杀', confirm_status: 'user_confirmed' }),
    makeClip({ clip_id: 'c2', label: '待调回合', confirm_status: 'pending', start: 300, end: 340 }),
  ]

  it('点击「待调」tab 只显示待处理切片', () => {
    render(<ClipList {...defaultProps} clips={clips} />)
    fireEvent.click(screen.getByText('待调 1'))
    expect(screen.queryByText('精彩击杀')).toBeNull()
    expect(screen.getByText('待调回合')).toBeTruthy()
  })

  it('搜索关键词过滤切片', async () => {
    render(<ClipList {...defaultProps} clips={clips} />)
    const input = screen.getByPlaceholderText('搜索切片 / 房间名')
    await userEvent.type(input, '击杀')
    expect(screen.getByText('精彩击杀')).toBeTruthy()
    expect(screen.queryByText('待调回合')).toBeNull()
  })

  it('搜索无匹配时显示空态', async () => {
    render(<ClipList {...defaultProps} clips={clips} />)
    const input = screen.getByPlaceholderText('搜索切片 / 房间名')
    await userEvent.type(input, '不存在的关键词')
    expect(screen.queryByText('精彩击杀')).toBeNull()
    expect(screen.queryByText('待调回合')).toBeNull()
  })
})

// ─── 交互：导出 ──────────────────────────────────────────────────────

describe('ClipList 导出交互', () => {
  it('已确认切片可导出，点击导出按钮回调 onExport', () => {
    const onExport = vi.fn()
    render(<ClipList {...defaultProps} clips={[makeClip()]} onExport={onExport} />)
    const exportBtn = document.querySelector('.clip-row-v2__acts .act-primary')
    expect(exportBtn).toBeTruthy()
    fireEvent.click(exportBtn!)
    expect(onExport).toHaveBeenCalledWith(expect.objectContaining({ clip_id: 'clip-001' }))
  })

  it('pending 状态切片导出按钮禁用（无 confirmAndExport）', () => {
    render(<ClipList {...defaultProps} clips={[makeClip({ confirm_status: 'pending' })]} />)
    const exportBtn = document.querySelector('.clip-row-v2__acts .act-primary') as HTMLButtonElement | null
    // 按钮要么不存在要么 disabled
    if (exportBtn) {
      expect(exportBtn.disabled).toBe(true)
    }
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
