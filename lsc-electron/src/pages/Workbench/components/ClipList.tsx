import { useMemo, useRef, useState, useCallback } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Card, List, Button, Empty, Checkbox, Tooltip, Dropdown } from 'antd'
import {
  DeleteOutlined,
  ExportOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  CloseOutlined,
  CheckOutlined,
  ReloadOutlined,
  MoreOutlined,
  InfoCircleOutlined,
} from '@ant-design/icons'
import { ClipSegment } from '@/types'
import { formatTime } from '@/utils/time'
import { formatClipHoverTitle } from '@/utils/clipNaming'
import './ClipList.css'

/** 超过此数量启用窗口虚拟渲染（仍保留 content-visibility 兜底） */
const VIRTUALIZE_THRESHOLD = 40
/** Must match the compact card's CSS minimum height plus its list spacing. */
const ROW_HEIGHT = 80
const OVERSCAN = 6

/** Stable list identity: clip_id preferred, then round_key, then composite fallback. */
export function getClipStableId(clip: ClipSegment): string {
  return clip.clip_id || clip.round_key || `${clip.room_id}-${clip.start}-${clip.end}`
}

export interface ExportProgressInfo {
  percent: number
  elapsed: number
  total: number
}

interface ClipListProps {
  clips: ClipSegment[]
  onDelete: (clipId: string) => void
  onExport: (clip: ClipSegment) => void
  onExportMany?: (clips: ClipSegment[]) => void
  onOpenFile?: (path: string) => void
  onOpenFolder?: (path: string) => void
  onCancelExport?: (jobId: string) => void
  exportProgress?: Record<string, ExportProgressInfo>
  onSelectClip?: (clip: ClipSegment) => void
  onConfirmClip?: (clip: ClipSegment) => void
  onConfirmAndExport?: (clip: ClipSegment) => void
  refiningClipId?: string | null
  selectedClipIds?: Set<string>
  onSelectedClipIdsChange?: (ids: Set<string>) => void
  /** 批量确认全部待调切片（不改边界） */
  onConfirmAll?: (clips: ClipSegment[]) => void
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  if (m > 0) return `${m}:${s.toString().padStart(2, '0')}`
  return `0:${s.toString().padStart(2, '0')}`
}

function needsConfirm(clip: ClipSegment): boolean {
  return clip.confirm_status === 'pending' || clip.confirm_status === 'refining'
}

/** 音频路径产出的回合，需要 OCR 复核后才能导出 */
function needsOcrReview(clip: ClipSegment): boolean {
  return clip.confirm_status === 'audio_pending'
}

/** 需要用户/后端继续处理：待确认边界 或 待 OCR 复核（用于「待调」tab 与计数） */
function needsAttention(clip: ClipSegment): boolean {
  return needsConfirm(clip) || needsOcrReview(clip)
}

function canExportClip(clip: ClipSegment): boolean {
  const confirmed = !clip.confirm_status ||
    clip.confirm_status === 'user_confirmed' ||
    clip.confirm_status === 'ocr_confirmed' ||
    clip.confirm_status === 'vision_confirmed'
  if (!confirmed) return false
  // audio_pending 需要 OCR 复核后才能导出
  if (needsOcrReview(clip)) return false
  if (clip.export_status === 'queued' || clip.export_status === 'exporting') return false
  return true
}

/** 单条「确认并导出」可用；批量 actionableClips 不得使用此函数 */
function canExportOrConfirmExport(clip: ClipSegment, hasConfirmAndExport: boolean): boolean {
  if (clip.export_status === 'queued' || clip.export_status === 'exporting') return false
  if (canExportClip(clip)) return true
  return hasConfirmAndExport && needsConfirm(clip)
}

/** 状态 → 色轨修饰类（语义：待调=琥珀 / AI=紫 / 可导=品牌青 / 已导=绿 / 失败=红 / 进行中=青 / 音频待复核=橙） */
function railClass(clip: ClipSegment, isRefining: boolean, isExporting: boolean): string {
  if (isExporting || clip.export_status === 'queued') return 'rail-busy'
  if (clip.export_status === 'failed') return 'rail-failed'
  if (clip.confirm_status === 'audio_pending') return 'rail-audio-pending'
  if (isRefining || clip.confirm_status === 'pending') return 'rail-pending'
  if (clip.confirm_status === 'ocr_confirmed' || clip.confirm_status === 'vision_confirmed') return 'rail-ai'
  if (clip.exported) return 'rail-exported'
  return 'rail-ready'
}

export function ClipList({ clips, onDelete, onExport, onExportMany, onOpenFile, onOpenFolder, onCancelExport, exportProgress, onSelectClip, onConfirmClip, onConfirmAndExport, refiningClipId, selectedClipIds: externalSelected, onSelectedClipIdsChange, onConfirmAll }: ClipListProps) {
  const [internalSelected, setInternalSelected] = useState<Set<string>>(new Set())
  const controlled = externalSelected != null
  const selectedClipIds = controlled ? externalSelected : internalSelected

  /** 列表筛选：全部 / 仅待调 */
  const [filter, setFilter] = useState<'all' | 'pending'>('all')

  const setSelectedClipIds = (updater: Set<string> | ((prev: Set<string>) => Set<string>)) => {
    const next = typeof updater === 'function' ? updater(selectedClipIds) : updater
    if (!controlled) setInternalSelected(next)
    onSelectedClipIdsChange?.(next)
  }

  const hasConfirmAndExport = !!onConfirmAndExport
  const actionableClips = useMemo(
    () => clips.filter(c => canExportOrConfirmExport(c, hasConfirmAndExport)),
    [clips, hasConfirmAndExport],
  )
  const selectedClips = useMemo(
    () => clips.filter(c => selectedClipIds.has(getClipStableId(c))),
    [clips, selectedClipIds],
  )
  const selectedActionable = useMemo(
    () => selectedClips.filter(c => canExportOrConfirmExport(c, hasConfirmAndExport)),
    [selectedClips, hasConfirmAndExport],
  )
  const pendingCount = useMemo(() => clips.filter(needsAttention).length, [clips])
  const confirmAllClips = useMemo(() => clips.filter(needsConfirm), [clips])
  const multiRoom = useMemo(() => new Set(clips.map(c => c.room_id)).size > 1, [clips])
  const filteredClips = useMemo(
    () => (filter === 'all' ? clips : clips.filter(needsAttention)),
    [clips, filter],
  )

  const scrollRef = useRef<HTMLDivElement>(null)
  const useVirtual = filteredClips.length >= VIRTUALIZE_THRESHOLD

  // 使用 @tanstack/react-virtual 实现高效虚拟列表
  const virtualizer = useVirtualizer({
    count: filteredClips.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: OVERSCAN,
    enabled: useVirtual,
  })

  const toggleSelected = useCallback((clipId: string, checked: boolean) => {
    setSelectedClipIds(prev => {
      const next = new Set(prev)
      if (checked) next.add(clipId)
      else next.delete(clipId)
      return next
    })
  }, [setSelectedClipIds])

  const renderClipRow = useCallback((clip: ClipSegment) => {
    const clipId = getClipStableId(clip)
    const prog = clip.job_id ? exportProgress?.[clip.job_id] : undefined
    const isExporting = !!prog || clip.export_status === 'exporting'
    const isRefining = clip.confirm_status === 'refining' ||
      (refiningClipId != null && (clip.clip_id === refiningClipId || clip.round_key === refiningClipId))
    const awaitingConfirm = needsConfirm(clip)
    const exportAllowed = canExportClip(clip)
    const confirmAndExportAllowed = canExportOrConfirmExport(clip, !!onConfirmAndExport)
    const isApprox = clip.mark_precision === 'approximate' ||
      (clip.mark_precision !== 'exact' &&
        !clip.clip_snapshot_id &&
        (clip.mark_in_wallclock == null || clip.mark_out_wallclock == null))
    const isAI = clip.confirm_status === 'ocr_confirmed' || clip.confirm_status === 'vision_confirmed'
    const hoverTitle = formatClipHoverTitle(clip.label || '切片', {
      roomName: clip.room_name,
      start: clip.start,
      end: clip.end,
      formatTime,
    })
      + (isApprox ? ' · 近似定位' : '')
      + (clip.boundary_evidence?.length ? `\n${clip.boundary_evidence.join(' · ')}` : '')
      + (clip.export_status === 'failed' && clip.export_error ? `\n${clip.export_error}` : '')

    return (
      <div
        key={clipId}
        onClick={() => !isRefining && onSelectClip?.(clip)}
        className={`clip-row-v2 ${railClass(clip, isRefining, isExporting)}${selectedClipIds.has(clipId) ? ' is-sel' : ''}${isRefining ? ' is-refining' : ''}`}
        style={useVirtual ? {
          position: 'absolute' as const,
          top: 0,
          left: 0,
          right: 0,
          height: ROW_HEIGHT - 4,
        } : {
          contentVisibility: 'auto',
          containIntrinsicSize: `0 ${ROW_HEIGHT}px`,
        }}
      >
        <span className="clip-row-v2__rail" />
        <Checkbox
          checked={selectedClipIds.has(clipId)}
          onClick={e => e.stopPropagation()}
          onChange={e => toggleSelected(clipId, e.target.checked)}
          style={{ flexShrink: 0, alignSelf: 'center', marginLeft: 2 }}
        />
        <div className="clip-row-v2__main">
          <div className="clip-row-v2__top">
            {multiRoom && (
              <span className="clip-row-v2__room" title={clip.room_name ?? clip.room_id ?? undefined}>
                {clip.room_name || clip.room_id}
              </span>
            )}
            <Tooltip title={hoverTitle} placement="top" mouseEnterDelay={0.25}>
              <span className="clip-row-v2__label">{clip.label}</span>
            </Tooltip>
            {isAI && <span className="clip-row-v2__tag clip-row-v2__tag--ai">AI</span>}
            {needsOcrReview(clip) && (
              <Tooltip title="音频路径检测到，等待 OCR 复核边界；复核完成后会自动升格，无需手动确认">
                <span className="clip-row-v2__tag clip-row-v2__tag--audio-pending">OCR 复核中</span>
              </Tooltip>
            )}
            {clip.confirm_status === 'user_confirmed' && (
              <span className="clip-row-v2__tag clip-row-v2__tag--confirmed">已确认</span>
            )}
            {isApprox && (
              <Tooltip title="近似定位：边界为音频推断，建议精修后导出">
                <span className="clip-row-v2__tag clip-row-v2__tag--approx">近似</span>
              </Tooltip>
            )}
          </div>
          <div className="clip-row-v2__bottom" onClick={e => e.stopPropagation()}>
              {isExporting ? (
                <span className="clip-row-v2__prog">
                  <span className="pbar-line">
                    {(prog?.percent ?? 0) > 0
                      ? <span className="pbar-fill" style={{ width: `${prog!.percent.toFixed(0)}%` }} />
                      : <span className="pbar-ind" />}
                  </span>
                  <span className="clip-row-v2__pct">
                    {(prog?.percent ?? 0) > 0 ? `${prog!.percent.toFixed(0)}%` : '准备中'}
                  </span>
                </span>
            ) : (
              <span className="clip-row-v2__time">
                {formatTime(clip.start)}<i className="sep-dot">→</i>{formatTime(clip.end)}<i className="sep-dot">·</i><span className="dur">{formatDuration(clip.end - clip.start)}</span>
              </span>
            )}
            {clip.boundary_evidence?.length ? (
              <Tooltip title={clip.boundary_evidence.join('\n')} placement="top">
                <InfoCircleOutlined style={{ fontSize: 12, color: 'var(--text-tertiary)', flexShrink: 0, cursor: 'help' }} />
              </Tooltip>
            ) : null}
            <span className="clip-row-v2__acts">
              {(isRefining || clip.confirm_status === 'pending') && onConfirmClip && (
                <Tooltip title="确认边界后即可导出" placement="top">
                  <Button
                    type="text"
                    size="small"
                    className="act-warn"
                    icon={<CheckOutlined />}
                    onClick={() => onConfirmClip(clip)}
                  />
                </Tooltip>
              )}
              {!isExporting && clip.export_status !== 'queued' && (
                <Tooltip
                  placement="top"
                  title={
                    !confirmAndExportAllowed
                      ? '请先确认后再导出'
                      : awaitingConfirm && onConfirmAndExport
                        ? '确认并导出'
                        : clip.export_status === 'failed' ? '重新导出' : '导出'
                  }
                >
                  <Button
                    size="small"
                    className={clip.export_status === 'failed' ? '' : 'act-primary'}
                    type={clip.export_status === 'failed' ? 'text' : 'primary'}
                    icon={clip.export_status === 'failed' ? <ReloadOutlined /> : <ExportOutlined />}
                    disabled={!confirmAndExportAllowed}
                    onClick={() => {
                      if (awaitingConfirm && onConfirmAndExport) onConfirmAndExport(clip)
                      else if (exportAllowed) onExport(clip)
                    }}
                  />
                </Tooltip>
              )}
              {(isExporting || clip.export_status === 'queued') && onCancelExport && clip.job_id && (
                <Tooltip title="取消导出" placement="top">
                  <Button
                    type="text"
                    size="small"
                    icon={<CloseOutlined />}
                    danger
                    onClick={() => { if (clip.job_id) onCancelExport(clip.job_id) }}
                  />
                </Tooltip>
              )}
              {clip.exported && clip.outputPath && (
                <>
                  <Tooltip title="打开文件" placement="top">
                    <Button
                      type="text"
                      size="small"
                      icon={<FolderOpenOutlined />}
                      onClick={() => onOpenFile?.(clip.outputPath!)}
                    />
                  </Tooltip>
                  <Tooltip title="打开目录" placement="top">
                    <Button
                      type="text"
                      size="small"
                      icon={<FolderOutlined />}
                      onClick={() => onOpenFolder?.(clip.outputPath!)}
                    />
                  </Tooltip>
                </>
              )}
              <Tooltip title="删除" placement="top">
                <Button
                  type="text"
                  size="small"
                  icon={<DeleteOutlined />}
                  danger
                  onClick={() => onDelete(clipId)}
                />
              </Tooltip>
            </span>
          </div>
        </div>
      </div>
    )
  }, [exportProgress, refiningClipId, selectedClipIds, onConfirmAndExport, onConfirmClip, onSelectClip, onDelete, onExport, onCancelExport, onOpenFile, toggleSelected, useVirtual, multiRoom])

  return (
    <Card
      size="small"
      className="clip-list-v2"
      title={
        <span className="clip-card-head">
          <span>切片列表<span className="clip-title-num">· {clips.length}</span></span>
          <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
            <span className="seg-mini">
              <button className={filter === 'all' ? 'on' : ''} onClick={() => setFilter('all')}>全部</button>
              <button className={filter === 'pending' ? 'on' : ''} onClick={() => setFilter('pending')}>
                待调{pendingCount > 0 ? ` ${pendingCount}` : ''}
              </button>
            </span>
            <Dropdown
              trigger={['click']}
              placement="bottomRight"
              menu={{
                items: [
                  {
                    key: 'export-all',
                    icon: <ExportOutlined />,
                    label: `导出全部（${actionableClips.length}）`,
                    disabled: actionableClips.length === 0,
                    onClick: () => onExportMany?.(actionableClips),
                  },
                  {
                    key: 'export-sel',
                    icon: <ExportOutlined />,
                    label: `导出所选（${selectedActionable.length}）`,
                    disabled: selectedActionable.length === 0,
                    onClick: () => onExportMany?.(selectedActionable),
                  },
                  { type: 'divider' },
                  {
                    key: 'confirm-all',
                    icon: <CheckOutlined />,
                    // 仅确认待确认边界的回合；audio_pending 等 OCR 复核自动升格，批量确认会跳过复核
                    label: `确认全部（${confirmAllClips.length}）`,
                    disabled: confirmAllClips.length === 0,
                    onClick: () => onConfirmAll?.(confirmAllClips),
                  },
                ],
              }}
            >
              <Button type="text" size="small" icon={<MoreOutlined />} title="批量操作" />
            </Dropdown>
          </span>
        </span>
      }
      style={{
        margin: '8px 16px 16px',
        flex: 1,
        minHeight: 0,
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--bg-secondary)',
      }}
      styles={{
        body: {
          flex: 1,
          minHeight: 0,
          overflow: 'hidden',
          padding: '0 6px 0',
          display: 'flex',
          flexDirection: 'column',
        }
      }}
    >
      {clips.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={<span style={{ color: 'var(--text-tertiary)' }}>暂无切片</span>}
          style={{ margin: '16px 0' }}
        />
      ) : filteredClips.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={<span style={{ color: 'var(--text-tertiary)' }}>没有待调切片</span>}
          style={{ margin: '16px 0' }}
        />
      ) : (
        <div
          ref={scrollRef}
          style={{ flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'hidden', position: 'relative' }}
        >
          {useVirtual ? (
            // 虚拟列表：只渲染可见行
            <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
              {virtualizer.getVirtualItems().map((virtualRow) => {
                const clip = filteredClips[virtualRow.index]
                return (
                  <div
                    key={virtualRow.key}
                    style={{
                      position: 'absolute',
                      top: 0,
                      left: 0,
                      width: '100%',
                      height: virtualRow.size,
                      transform: `translateY(${virtualRow.start}px)`,
                    }}
                  >
                    {renderClipRow(clip)}
                  </div>
                )
              })}
            </div>
          ) : (
            <List
              dataSource={filteredClips}
              split={false}
              rowKey={clip => getClipStableId(clip)}
              renderItem={(clip) => (
                <List.Item style={{ padding: 0, marginBottom: 0, border: 'none' }}>
                  {renderClipRow(clip)}
                </List.Item>
              )}
            />
          )}
        </div>
      )}
      <div className="clip-list-hint">单击定位与回看 · I/O 精调入出点</div>
    </Card>
  )
}
