import { useEffect, useMemo, useRef, useState } from 'react'
import { Card, List, Button, Space, Tag, Empty, Progress, Checkbox, Tooltip } from 'antd'
import { DeleteOutlined, ExportOutlined, FolderOpenOutlined, FolderOutlined, CloseCircleOutlined, CheckOutlined } from '@ant-design/icons'
import { ClipSegment, ClipConfirmStatus } from '@/types'
import { formatTime } from '@/utils/time'
import { formatClipHoverTitle } from '@/utils/clipNaming'

/** 超过此数量启用窗口虚拟渲染（仍保留 content-visibility 兜底） */
const VIRTUALIZE_THRESHOLD = 40
const ROW_HEIGHT = 88
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
    clip.confirm_status === 'ocr_confirmed' ||
    clip.confirm_status === 'vision_confirmed'
  if (!confirmed) return false
  if (clip.export_status === 'queued' || clip.export_status === 'exporting') return false
  return true
}

/** 单条「确认并导出」可用；批量 actionableClips 不得使用此函数 */
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
    case 'vision_confirmed': return { text: '视觉确认', color: 'geekblue' }
    default:
      if (clip.exported) return { text: '已导', color: 'green' }
      return null
  }
}

export function ClipList({ clips, onDelete, onExport, onExportMany, onOpenFile, onOpenFolder, onCancelExport, exportProgress, onSelectClip, onConfirmClip, onConfirmAndExport, refiningClipId, selectedClipIds: externalSelected, onSelectedClipIdsChange }: ClipListProps) {
  const [internalSelected, setInternalSelected] = useState<Set<string>>(new Set())
  const controlled = externalSelected != null
  const selectedClipIds = controlled ? externalSelected : internalSelected

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
    [selectedClipIds, clips],
  )
  const selectedActionable = useMemo(
    () => selectedClips.filter(c => canExportOrConfirmExport(c, hasConfirmAndExport)),
    [selectedClips, hasConfirmAndExport],
  )
  const pendingCount = useMemo(() => clips.filter(needsConfirm).length, [clips])

  const [scrollTop, setScrollTop] = useState(0)
  const [viewportH, setViewportH] = useState(480)
  const scrollRef = useRef<HTMLDivElement>(null)
  const useVirtual = clips.length >= VIRTUALIZE_THRESHOLD
  const visibleRange = useMemo(() => {
    if (!useVirtual) return [0, clips.length] as const
    const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN)
    const end = Math.min(clips.length, Math.ceil((scrollTop + viewportH) / ROW_HEIGHT) + OVERSCAN)
    return [start, end] as const
  }, [useVirtual, scrollTop, viewportH, clips.length])
  const visibleClips = useMemo(
    () => (useVirtual ? clips.slice(visibleRange[0], visibleRange[1]) : clips),
    [clips, useVirtual, visibleRange],
  )

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const update = () => setViewportH(el.clientHeight || 480)
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [clips.length])

  const toggleSelected = (clipId: string, checked: boolean) => {
    setSelectedClipIds(prev => {
      const next = new Set(prev)
      if (checked) next.add(clipId)
      else next.delete(clipId)
      return next
    })
  }

  const renderClipRow = (clip: ClipSegment, indexOffset = 0) => {
            const clipId = getClipStableId(clip)
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
              + (clip.boundary_evidence?.length ? `\n${clip.boundary_evidence.join(' · ')}` : '')
              + (isFailed && clip.export_error ? `\n${clip.export_error}` : '')

            const accent = isRefining
              ? 'var(--brand-500, #007aff)'
              : awaitingConfirm
                ? 'var(--state-warning-dark, #ff9f0a)'
                : 'transparent'

            return (
              <div
                key={clipId}
                onClick={() => !isRefining && onSelectClip?.(clip)}
                style={{
                  padding: 0,
                  marginBottom: 4,
                  border: 'none',
                  cursor: onSelectClip ? 'pointer' : undefined,
                  // 浏览器原生“虚拟滚动”：跳过屏外布局/绘制
                  contentVisibility: 'auto',
                  containIntrinsicSize: '0 72px',
                  ...(useVirtual ? {
                    position: 'absolute' as const,
                    top: (visibleRange[0] + indexOffset) * ROW_HEIGHT,
                    left: 0,
                    right: 0,
                    height: ROW_HEIGHT - 4,
                  } : {}),
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
                      checked={selectedClipIds.has(clipId)}
                      onClick={e => e.stopPropagation()}
                      onChange={e => toggleSelected(clipId, e.target.checked)}
                      style={{ flexShrink: 0 }}
                    />
                    <Tooltip title={hoverTitle} placement="top" mouseEnterDelay={0.25}>
                      <span style={{
                        flex: 1,
                        minWidth: 0,
                        fontWeight: 560,
                        fontSize: 13,
                        color: 'var(--text-primary)',
                        whiteSpace: 'normal',
                        wordBreak: 'break-word',
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
                    {isApprox && (
                      <Tag color="orange" style={{ margin: 0, flexShrink: 0, lineHeight: '18px', padding: '0 6px' }}>
                        近似
                      </Tag>
                    )}
                  </div>

                  {/* 第二行：时间段 + 操作按钮（不溢出） */}
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    flexWrap: 'wrap',
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
                      {clip.boundary_evidence?.length ? (
                        <span style={{ opacity: 0.7, marginLeft: 6 }}>
                          {clip.boundary_evidence.slice(0, 2).join(' · ')}
                        </span>
                      ) : null}
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
                            onClick={() => onConfirmClip(clip)}
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
                              if (awaitingConfirm && onConfirmAndExport) onConfirmAndExport(clip)
                              else if (exportAllowed) onExport(clip)
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
                          onClick={() => onDelete(clipId)}
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
              </div>
            )
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
          overflow: 'hidden',
          padding: '0 6px 6px',
          display: 'flex',
          flexDirection: 'column',
        }
      }}
      extra={
        <Space size={6} wrap>
          {clips.length > 0 && onExportMany && (
            <>
              <Tooltip
                title={actionableClips.length === 0
                  ? (pendingCount > 0 ? '请先确认待调整的切片' : '没有可导出的切片')
                  : undefined}
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
            共 {clips.length}{pendingCount > 0 ? ` · 待确认 ${pendingCount}` : ''}
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
        <div
          ref={scrollRef}
          onScroll={(e) => {
            if (!useVirtual) return
            setScrollTop((e.target as HTMLDivElement).scrollTop)
          }}
          style={{ flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'hidden', position: 'relative' }}
        >
          {useVirtual ? (
            <div style={{ height: clips.length * ROW_HEIGHT, position: 'relative' }}>
              {visibleClips.map((clip, i) => renderClipRow(clip, i))}
            </div>
          ) : (
            <List
              dataSource={clips}
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
    </Card>
  )
}
