import { useMemo, useState } from 'react'
import { Card, List, Button, Space, Tag, Empty, Progress, Checkbox, Tooltip } from 'antd'
import { DeleteOutlined, ExportOutlined, FolderOpenOutlined, FolderOutlined, CloseCircleOutlined, CheckOutlined } from '@ant-design/icons'
import { ClipSegment, ClipConfirmStatus } from '@/types'
import { formatTime } from '@/utils/time'
import { formatClipHoverTitle } from '@/utils/clipNaming'

export interface ExportProgressInfo {
  percent: number
  elapsed: number
  total: number
}

interface ClipListProps {
  clips: ClipSegment[]
  onDelete: (index: number) => void
  onExport: (clip: ClipSegment, index: number) => void
  onExportMany?: (clips: ClipSegment[]) => void
  onOpenFile?: (path: string) => void
  onOpenFolder?: (path: string) => void
  onCancelExport?: (jobId: string) => void
  exportProgress?: Record<string, ExportProgressInfo>
  onSelectClip?: (clip: ClipSegment, index: number) => void
  onConfirmClip?: (clip: ClipSegment, index: number) => void
  onConfirmAndExport?: (clip: ClipSegment, index: number) => void
  refiningClipId?: string | null
  selectedIndices?: Set<number>
  onSelectedIndicesChange?: (indices: Set<number>) => void
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

function canExportClip(clip: ClipSegment): boolean {
  const confirmed = !clip.confirm_status ||
    clip.confirm_status === 'user_confirmed' ||
    clip.confirm_status === 'ocr_confirmed'
  if (!confirmed) return false
  if (clip.export_status === 'queued' || clip.export_status === 'exporting') return false
  return true
}

function canExportOrConfirmExport(clip: ClipSegment, hasConfirmAndExport: boolean): boolean {
  if (clip.export_status === 'queued' || clip.export_status === 'exporting') return false
  if (canExportClip(clip)) return true
  return hasConfirmAndExport && needsConfirm(clip)
}

/** 只保留一个最关键状态，少占横向空间 */
function primaryStatus(
  clip: ClipSegment,
  isRefining: boolean,
  isExporting: boolean,
  progPercent?: number,
): { text: string; color: string } | null {
  if (isExporting) {
    return { text: progPercent != null ? `${progPercent.toFixed(0)}%` : '导出', color: 'blue' }
  }
  if (clip.export_status === 'queued') return { text: '排队', color: 'default' }
  if (clip.export_status === 'failed') return { text: '失败', color: 'red' }
  if (isRefining) return { text: '调整中', color: 'blue' }
  switch (clip.confirm_status as ClipConfirmStatus | undefined) {
    case 'pending': return { text: '待调', color: 'orange' }
    case 'user_confirmed': return { text: '可导', color: 'cyan' }
    case 'ocr_confirmed': return { text: 'AI可导', color: 'purple' }
    default:
      if (clip.exported) return { text: '已导', color: 'green' }
      return null
  }
}

export function ClipList({ clips, onDelete, onExport, onExportMany, onOpenFile, onOpenFolder, onCancelExport, exportProgress, onSelectClip, onConfirmClip, onConfirmAndExport, refiningClipId, selectedIndices: externalSelected, onSelectedIndicesChange }: ClipListProps) {
  const [internalSelected, setInternalSelected] = useState<Set<number>>(new Set())
  const controlled = externalSelected != null
  const selectedIndices = controlled ? externalSelected : internalSelected

  const setSelectedIndices = (updater: Set<number> | ((prev: Set<number>) => Set<number>)) => {
    const next = typeof updater === 'function' ? updater(selectedIndices) : updater
    if (!controlled) setInternalSelected(next)
    onSelectedIndicesChange?.(next)
  }

  const hasConfirmAndExport = !!onConfirmAndExport
  const actionableClips = useMemo(
    () => clips.filter(c => canExportOrConfirmExport(c, hasConfirmAndExport)),
    [clips, hasConfirmAndExport],
  )
  const selectedClips = useMemo(
    () => [...selectedIndices].sort((a, b) => a - b).map(i => clips[i]).filter(Boolean),
    [selectedIndices, clips],
  )
  const selectedActionable = useMemo(
    () => selectedClips.filter(c => canExportOrConfirmExport(c, hasConfirmAndExport)),
    [selectedClips, hasConfirmAndExport],
  )
  const pendingCount = useMemo(() => clips.filter(needsConfirm).length, [clips])

  const toggleSelected = (index: number, checked: boolean) => {
    setSelectedIndices(prev => {
      const next = new Set(prev)
      if (checked) next.add(index)
      else next.delete(index)
      return next
    })
  }

  return (
    <Card
      size="small"
      title="切片列表"
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
          overflow: 'auto',
          padding: '0 6px 6px',
        }
      }}
      extra={
        <Space size={6}>
          {clips.length > 0 && onExportMany && (
            <>
              <Tooltip
                title={actionableClips.length === 0
                  ? (pendingCount > 0 ? '请先确认待调整的切片' : '没有可导出的切片')
                  : (pendingCount > 0 && actionableClips.some(needsConfirm)
                    ? '待确认切片将先确认再导出'
                    : undefined)}
              >
                <Button
                  type="link"
                  size="small"
                  disabled={actionableClips.length === 0}
                  onClick={() => onExportMany(actionableClips)}
                >
                  导出全部
                </Button>
              </Tooltip>
              <Tooltip
                title={selectedClips.length === 0
                  ? '请先勾选切片'
                  : selectedActionable.length === 0
                    ? '所选切片需先确认或正在导出'
                    : undefined}
              >
                <Button
                  type="link"
                  size="small"
                  disabled={selectedActionable.length === 0}
                  onClick={() => onExportMany(selectedActionable)}
                >
                  导出所选
                </Button>
              </Tooltip>
            </>
          )}
          <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
            {clips.length}{pendingCount > 0 ? `/${pendingCount}待` : ''}
          </span>
        </Space>
      }
    >
      {clips.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="暂无切片"
          style={{ margin: '16px 0' }}
        />
      ) : (
        <List
          dataSource={clips}
          split={false}
          renderItem={(clip, index) => {
            const prog = clip.job_id ? exportProgress?.[clip.job_id] : undefined
            const isExporting = !!prog || clip.export_status === 'exporting'
            const isQueued = clip.export_status === 'queued'
            const isFailed = clip.export_status === 'failed'
            const isRefining = clip.confirm_status === 'refining' ||
              (refiningClipId != null && (clip.clip_id === refiningClipId || clip.round_key === refiningClipId))
            const awaitingConfirm = needsConfirm(clip)
            const exportAllowed = canExportClip(clip)
            const confirmAndExportAllowed = canExportOrConfirmExport(clip, !!onConfirmAndExport)
            const status = primaryStatus(clip, isRefining, isExporting, prog?.percent)
            const isApprox = clip.mark_precision === 'approximate' ||
              (clip.mark_precision !== 'exact' &&
                !clip.clip_snapshot_id &&
                (clip.mark_in_wallclock == null || clip.mark_out_wallclock == null))
            const hoverTitle = formatClipHoverTitle(clip.label || '切片', {
              roomName: clip.room_name,
              start: clip.start,
              end: clip.end,
              formatTime,
            })
              + (isApprox ? ' · 近似定位' : '')
              + (isFailed && clip.export_error ? `\n${clip.export_error}` : '')

            const accent = isRefining
              ? 'var(--brand-500, #007aff)'
              : awaitingConfirm
                ? 'var(--state-warning-dark, #ff9f0a)'
                : 'transparent'

            return (
              <List.Item
                onClick={() => !isRefining && onSelectClip?.(clip, index)}
                style={{
                  padding: 0,
                  marginBottom: 4,
                  border: 'none',
                  cursor: onSelectClip ? 'pointer' : undefined,
                }}
              >
                <div style={{
                  width: '100%',
                  maxWidth: '100%',
                  boxSizing: 'border-box',
                  overflow: 'hidden',
                  padding: '8px 10px',
                  background: 'var(--bg-tertiary)',
                  borderRadius: 6,
                  borderLeft: `3px solid ${accent}`,
                  boxShadow: isRefining
                    ? '0 0 0 1px rgba(0,122,255,0.35)'
                    : undefined,
                }}>
                  {/* 第一行：选择框 + 名字 */}
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    minWidth: 0,
                    width: '100%',
                  }}>
                    <Checkbox
                      checked={selectedIndices.has(index)}
                      onClick={e => e.stopPropagation()}
                      onChange={e => toggleSelected(index, e.target.checked)}
                      style={{ flexShrink: 0 }}
                    />
                    <Tooltip title={hoverTitle} placement="top" mouseEnterDelay={0.25}>
                      <span style={{
                        flex: 1,
                        minWidth: 0,
                        fontWeight: 560,
                        fontSize: 13,
                        color: 'var(--text-primary)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}>
                        {clip.label}
                      </span>
                    </Tooltip>
                    {status && (
                      <Tag
                        color={status.color === 'default' ? undefined : status.color}
                        style={{ margin: 0, flexShrink: 0, lineHeight: '18px', padding: '0 6px' }}
                      >
                        {status.text}
                      </Tag>
                    )}
                  </div>

                  {/* 第二行：时间段 + 操作按钮（不溢出） */}
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 8,
                    marginTop: 6,
                    marginLeft: 24,
                    minWidth: 0,
                    width: 'calc(100% - 24px)',
                    boxSizing: 'border-box',
                  }}>
                    <span style={{
                      flex: '1 1 auto',
                      minWidth: 0,
                      fontSize: 11,
                      color: 'var(--text-tertiary)',
                      fontFamily: 'var(--font-mono, ui-monospace, monospace)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}>
                      {formatTime(clip.start)}–{formatTime(clip.end)}
                      <span style={{ opacity: 0.55, marginLeft: 6 }}>{formatDuration(clip.end - clip.start)}</span>
                    </span>

                    <Space
                      size={0}
                      style={{ flexShrink: 0, maxWidth: '100%' }}
                      onClick={e => e.stopPropagation()}
                    >
                      {(isRefining || clip.confirm_status === 'pending') && onConfirmClip && (
                        <Tooltip title="确认" placement="top">
                          <Button
                            type={isRefining ? 'primary' : 'text'}
                            size="small"
                            icon={<CheckOutlined />}
                            onClick={() => onConfirmClip(clip, index)}
                          />
                        </Tooltip>
                      )}
                      {!isExporting && (
                        <Tooltip
                          placement="top"
                          title={
                            !confirmAndExportAllowed
                              ? (isQueued ? '已在队列中' : '请先确认后再导出')
                              : awaitingConfirm && onConfirmAndExport
                                ? '确认并导出'
                                : isFailed ? '重新导出' : '导出'
                          }
                        >
                          <Button
                            type={awaitingConfirm && onConfirmAndExport ? 'primary' : 'text'}
                            size="small"
                            icon={<ExportOutlined />}
                            disabled={!confirmAndExportAllowed}
                            onClick={() => {
                              if (awaitingConfirm && onConfirmAndExport) onConfirmAndExport(clip, index)
                              else if (exportAllowed) onExport(clip, index)
                            }}
                          />
                        </Tooltip>
                      )}
                      {isExporting && onCancelExport && clip.job_id && (
                        <Tooltip title="取消导出" placement="top">
                          <Button
                            type="text"
                            size="small"
                            icon={<CloseCircleOutlined />}
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
                          onClick={() => onDelete(index)}
                        />
                      </Tooltip>
                    </Space>
                  </div>

                  {isExporting && prog && (
                    <Progress
                      percent={prog.percent}
                      size="small"
                      status="active"
                      showInfo={false}
                      style={{ margin: '4px 0 0 24px', maxWidth: 'calc(100% - 24px)' }}
                    />
                  )}
                </div>
              </List.Item>
            )
          }}
        />
      )}
    </Card>
  )
}
