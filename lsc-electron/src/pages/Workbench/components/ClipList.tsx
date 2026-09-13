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
} from '@ant-design/icons'
import { ClipSegment } from '@/types'
import { formatTime } from '@/utils/time'
import { formatClipHoverTitle } from '@/utils/clipNaming'
import {
  canExportOrConfirmExport,
  clipExportState,
  CLIP_EXPORT_STATE_HINT,
  CLIP_EXPORT_STATE_LABEL,
  type ClipExportStateCode,
} from '@/utils/clipExportPolicy'
import { useI18n } from '@/i18n'
import './ClipList.css'

/** 超过此数量启用窗口虚拟渲染（仍保留 content-visibility 兜底） */
const VIRTUALIZE_THRESHOLD = 40
/** Must match the compact card's CSS minimum height plus its list spacing. */
const ROW_HEIGHT = 80
const OVERSCAN = 6

import { canExportClip as canExportClipPolicy } from '@/utils/clipExportPolicy'

/** Stable list identity: clip_id preferred, then round_key, then composite fallback. */
export function getClipStableId(clip: ClipSegment): string {
  return clip.clip_id || clip.round_key || `${clip.room_id}-${clip.start}-${clip.end}`
}

export function canExportClip(clip: ClipSegment): boolean {
  // clip.confirm_status === 'vision_confirmed'
  return canExportClipPolicy(clip)
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
  onDeleteMany?: (clipIds: string[]) => void
  onClearExported?: () => void
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  if (m > 0) return `${m}:${s.toString().padStart(2, '0')}`
  return `0:${s.toString().padStart(2, '0')}`
}


/**
 * 判定切片来源：区分持续分析产出还是手动切片
 * 持续分析：携带 ai_highlight / round_key / boundary_source / 自动分析识别
 * 手动切片：用户在时间轴打点添加 (source === 'manual')
 * 切片精度支持 mark_precision (exact / approximate 近似)
 */
export function isContinuousAnalysisClip(clip: ClipSegment): boolean {
  if (clip.source === 'manual') return false
  if (clip.source === 'ai_highlight' || clip.is_ai_highlight) return true
  if (Boolean(clip.round_key) || Boolean(clip.boundary_source)) return true
  if (clip.confirm_status === 'ocr_confirmed' || clip.confirm_status === 'vision_confirmed' || clip.confirm_status === 'audio_pending') return true
  return false
}

function getActualRecordingRange(clip: ClipSegment): { start: number; end: number } | null {
  const start = clip.recording_start_sec
  const end = clip.recording_end_sec
  if (typeof start !== 'number' || !Number.isFinite(start)
    || typeof end !== 'number' || !Number.isFinite(end) || end <= start) {
    return null
  }
  return { start, end }
}

/**
 * 状态 → 色轨修饰类：可导出(青) / 导出中(青蓝) / 已导出(绿) / 失败(红) / 尚不可导出(琥珀)
 *
 * 「尚不可导出」必须与「可导出」区分：此前待审计/需确认的切片也亮青色 rails-ready，
 * 用户到导出时才发现少了几条（2026-09-12 09:01 现场）。
 */
function railClass(clip: ClipSegment, _isRefining: boolean, isExporting: boolean): string {
  if (isExporting || clip.export_status === 'queued') return 'rail-busy'
  if (clip.export_status === 'failed') return 'rail-failed'
  if (clip.exported) return 'rail-exported'
  if (clipExportState(clip) !== 'EXPORTABLE') return 'rail-pending'
  return 'rail-ready'
}

/** 色轨含义的唯一说明源 */
export const RAIL_LEGEND: Record<string, string> = {
  'rail-ready': '可导出',
  'rail-busy': '正在导出',
  'rail-exported': '已导出完成',
  'rail-failed': '导出失败 · 可重试',
  'rail-pending': '尚不可导出（待审计/需确认/已排除，见行内标签）',
}

/** 状态标签的配色修饰类（与 policy 的 ClipExportStateCode 一一对应） */
const STATE_TAG_CLASS: Record<ClipExportStateCode, string> = {
  EXPORTABLE: '',
  PENDING_AUDIT: 'clip-row-v2__tag--pending',
  NEEDS_CONFIRM: 'clip-row-v2__tag--confirm',
  REJECTED: 'clip-row-v2__tag--rejected',
  BLOCKED: 'clip-row-v2__tag--blocked',
}

export function ClipList({ clips, onDelete, onExport, onExportMany, onOpenFile, onOpenFolder, onCancelExport, exportProgress, onSelectClip, onConfirmClip, onConfirmAndExport: _onConfirmAndExport, refiningClipId, selectedClipIds: externalSelected, onSelectedClipIdsChange, onConfirmAll: _onConfirmAll, onDeleteMany, onClearExported }: ClipListProps) {
  const { t } = useI18n()
  const [internalSelected, setInternalSelected] = useState<Set<string>>(new Set())
  const controlled = externalSelected != null
  const selectedClipIds = controlled ? externalSelected : internalSelected

  const setSelectedClipIds = (updater: Set<string> | ((prev: Set<string>) => Set<string>)) => {
    const next = typeof updater === 'function' ? updater(selectedClipIds) : updater
    if (!controlled) setInternalSelected(next)
    onSelectedClipIdsChange?.(next)
  }

  const actionableClips = useMemo(
    () => clips.filter(c => canExportOrConfirmExport(c)),
    [clips],
  )
  const selectedClips = useMemo(
    () => clips.filter(c => selectedClipIds.has(getClipStableId(c))),
    [clips, selectedClipIds],
  )
  const selectedActionable = useMemo(
    () => selectedClips.filter(c => canExportOrConfirmExport(c)),
    [selectedClips],
  )
  const multiRoom = useMemo(() => new Set(clips.map(c => c.room_id)).size > 1, [clips])
  const filteredClips = clips

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
    const isContinuous = isContinuousAnalysisClip(clip)
    // 切片状态已全部取消（包括 isApprox / clip-row-v2__tag--approx 近似），仅保留持续分析与手动切片
    const actualRange = getActualRecordingRange(clip)
    const shownStart = actualRange?.start ?? clip.start
    const shownEnd = actualRange?.end ?? clip.end
    const shownDuration = Math.max(0, shownEnd - shownStart)
    const hoverTitle = formatClipHoverTitle(clip.label || t('切片'), {
      roomName: clip.room_name,
      start: clip.start,
      end: clip.end,
      formatTime,
    })
      + ` · ${isContinuous ? t('持续分析') : t('手动切片')}`
      + `\n${formatTime(shownStart)}→${formatTime(shownEnd)}`
      + (clip.boundary_quality_reason_code
        ? `\n${t('边界质量')}: ${clip.boundary_quality ?? 'review'} (${clip.boundary_quality_reason_code})`
        : '')
      + (clip.export_status === 'failed' && clip.export_error ? `\n${clip.export_error}` : '')

    const rail = railClass(clip, isRefining, isExporting)
    const exportState = clipExportState(clip)

    return (
      <div
        key={clipId}
        onClick={() => !isRefining && onSelectClip?.(clip)}
        className={`clip-row-v2 ${rail}${selectedClipIds.has(clipId) ? ' is-sel' : ''}${isRefining ? ' is-refining' : ''}`}
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
        <span className="clip-row-v2__rail" title={t(RAIL_LEGEND[rail] ?? '')} />
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
            {isContinuous ? (
              <span className="clip-row-v2__tag clip-row-v2__tag--ai">{t('持续分析')}</span>
            ) : (
              <span className="clip-row-v2__tag clip-row-v2__tag--manual">{t('手动切片')}</span>
            )}
            {/* 非「可导出」状态逐条标出：用户不该等到导出才发现某条没进草稿 */}
            {exportState !== 'EXPORTABLE' && (
              <Tooltip title={t(CLIP_EXPORT_STATE_HINT[exportState])} placement="top">
                <span className={`clip-row-v2__tag ${STATE_TAG_CLASS[exportState]}`}>
                  {t(CLIP_EXPORT_STATE_LABEL[exportState])}
                </span>
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
                    {(prog?.percent ?? 0) > 0 ? `${prog!.percent.toFixed(0)}%` : t('准备中')}
                  </span>
                </span>
            ) : (
              <span className="clip-row-v2__time">
                {formatTime(shownStart)}<i className="sep-dot">→</i>{formatTime(shownEnd)}<i className="sep-dot">·</i><span className="dur">{formatDuration(shownDuration)}</span>
              </span>
            )}
            <span className="clip-row-v2__acts">
              {isRefining && onConfirmClip && (
                <Tooltip title={t('确认当前边界')} placement="top">
                  <span style={{ display: 'inline-flex' }}>
                    <Button
                      type="text"
                      size="small"
                      className="act-warn"
                      icon={<CheckOutlined />}
                      onClick={() => onConfirmClip(clip)}
                    />
                  </span>
                </Tooltip>
              )}
              {!isExporting && clip.export_status !== 'queued' && (
                <Tooltip
                  placement="top"
                  title={clip.export_status === 'failed' ? t('重新导出') : t('导出')}
                >
                  <Button
                    size="small"
                    className={clip.export_status === 'failed' ? '' : 'act-primary'}
                    type={clip.export_status === 'failed' ? 'text' : 'primary'}
                    icon={clip.export_status === 'failed' ? <ReloadOutlined /> : <ExportOutlined />}
                    onClick={() => onExport(clip)}
                  />
                </Tooltip>
              )}
              {(isExporting || clip.export_status === 'queued') && onCancelExport && clip.job_id && (
                <Tooltip title={t('取消导出')} placement="top">
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
                  <Tooltip title={t('打开文件')} placement="top">
                    <Button
                      type="text"
                      size="small"
                      icon={<FolderOpenOutlined />}
                      onClick={() => onOpenFile?.(clip.outputPath!)}
                    />
                  </Tooltip>
                  <Tooltip title={t('打开目录')} placement="top">
                    <Button
                      type="text"
                      size="small"
                      icon={<FolderOutlined />}
                      onClick={() => onOpenFolder?.(clip.outputPath!)}
                    />
                  </Tooltip>
                </>
              )}
              <Tooltip title={isExporting ? t('正在导出，请先取消再删除') : t('删除')} placement="top">
                <Button
                  type="text"
                  size="small"
                  icon={<DeleteOutlined />}
                  danger={!isExporting}
                  disabled={isExporting}
                  onClick={() => onDelete(clipId)}
                />
              </Tooltip>
            </span>
          </div>
        </div>
      </div>
    )
  }, [exportProgress, refiningClipId, selectedClipIds, onConfirmClip, onSelectClip, onDelete, onExport, onCancelExport, onOpenFile, toggleSelected, useVirtual, multiRoom])

  return (
    <Card
      size="small"
      className="clip-list-v2"
      title={
        <span className="clip-card-head">
          <span>{t('切片列表')}<span className="clip-title-num">· {clips.length}</span></span>
          <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
            <Dropdown
              trigger={['click']}
              placement="bottomRight"
              menu={{
                items: [
                  // 切片页取消待确认状态：无需单独“确认全部”，所有有效切片均直接支持导出全部
                  {
                    key: 'export-all',
                    icon: <ExportOutlined />,
                    label: t('导出全部（{count}）', { count: actionableClips.length }),
                    disabled: actionableClips.length === 0,
                    onClick: () => onExportMany?.(actionableClips),
                  },
                  {
                    key: 'export-sel',
                    icon: <ExportOutlined />,
                    label: t('导出所选（{count}）', { count: selectedActionable.length }),
                    disabled: selectedActionable.length === 0,
                    onClick: () => onExportMany?.(selectedActionable),
                  },
                  {
                    type: 'divider',
                  },
                  {
                    key: 'delete-sel',
                    icon: <DeleteOutlined />,
                    danger: true,
                    label: t('删除所选（{count}）', { count: selectedClipIds.size }),
                    disabled: selectedClipIds.size === 0,
                    onClick: () => {
                      const ids = Array.from(selectedClipIds)
                      onDeleteMany?.(ids)
                      setSelectedClipIds(new Set())
                    },
                  },
                  {
                    key: 'clear-exported',
                    icon: <DeleteOutlined />,
                    label: t('清除已导出切片（{count}）', {
                      count: clips.filter(c => c.exported).length,
                    }),
                    disabled: clips.filter(c => c.exported).length === 0,
                    onClick: () => onClearExported?.(),
                  },
                ],
              }}
            >
              <Button type="text" size="small" icon={<MoreOutlined />} title={t('批量操作')} />
            </Dropdown>
          </span>
        </span>
      }
      variant="borderless"
      style={{
        margin: 0,
        flex: 1,
        minHeight: 0,
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
        background: 'transparent',
        borderRadius: 0,
      }}
      styles={{
        header: {
          padding: '8px 12px',
          borderBottom: '1px solid var(--border-hairline)',
        },
        body: {
          flex: 1,
          minHeight: 0,
          overflow: 'hidden',
          padding: '4px 6px',
          display: 'flex',
          flexDirection: 'column',
        }
      }}
    >
      {clips.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={<span style={{ color: 'var(--text-tertiary)' }}>{t('暂无切片')}</span>}
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
      <div className="clip-list-hint">{t('单击定位与回看 · I/O 打标 · ,/. 微调 0.2s · [ ] 移出点 / Shift+[ ] 移入点 · Esc 退出精修')}</div>
    </Card>
  )
}
