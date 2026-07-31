/**
 * SplashScreen - 启动页 / 依赖安装页
 *
 * 应用启动时显示：
 * 1. 如果依赖已就绪 → 短暂显示 logo 后进入主界面
 * 2. 如果依赖缺失 → 显示安装进度，完成后自动进入主界面
 *
 * 进度事件来自 Electron 主进程的 dependency-progress IPC。
 */
import { useEffect, useState, useCallback } from 'react'
import { Progress, Button, Typography, Space, ConfigProvider, theme } from 'antd'
import { CheckCircleFilled, DownloadOutlined, ReloadOutlined, WarningOutlined } from '@ant-design/icons'
import './SplashScreen.css'

const { Title, Text } = Typography

/** 安装阶段定义 */
interface InstallPhase {
  key: string
  label: string
  status: 'pending' | 'running' | 'done' | 'error'
  percent: number
  detail?: string
}

const PHASE_DEFS: Array<{ key: string; label: string }> = [
  { key: 'python_core', label: 'Python 核心依赖' },
  { key: 'python_ai', label: 'AI 分析依赖' },
  { key: 'ffmpeg', label: 'FFmpeg 多媒体框架' },
]

/** 依赖状态 */
export interface DepCheckResult {
  python: Record<string, boolean>
  core_ok: boolean
  ai_ok: boolean
  ffmpeg_ok: boolean
  all_ok: boolean
}

export interface SplashScreenProps {
  /** 依赖检测完成回调（依赖已就绪） */
  onReady: () => void
  /** 是否由启动页自行检测。Electron 启动时由主进程统一检测，避免重复检测。 */
  autoCheck?: boolean
  initialResult?: DepCheckResult
  initialStatus?: 'checking' | 'installing' | 'ready' | 'error'
  initialError?: string
}

export default function SplashScreen({
  onReady,
  autoCheck = true,
  initialResult,
  initialStatus = 'checking',
  initialError = '',
}: SplashScreenProps) {
  const [phases, setPhases] = useState<InstallPhase[]>(
    PHASE_DEFS.map((p) => ({ ...p, status: 'pending', percent: 0 }))
  )
  const [overallPercent, setOverallPercent] = useState(0)
  const [status, setStatus] = useState<'checking' | 'installing' | 'error' | 'done'>(
    initialStatus === 'ready' ? 'done' : initialStatus
  )
  const [errorMsg, setErrorMsg] = useState(initialError)
  const [, setDepResult] = useState<DepCheckResult | null>(initialResult ?? null)

  useEffect(() => {
    if (!initialResult) return
    setDepResult(initialResult)
    setPhases((prev) =>
      prev.map((phase) => {
        const ready = phase.key === 'python_core'
          ? initialResult.core_ok
          : phase.key === 'python_ai'
            ? initialResult.ai_ok
            : initialResult.ffmpeg_ok
        return { ...phase, status: ready ? 'done' : 'pending', percent: ready ? 100 : 0 }
      })
    )
  }, [initialResult])

  useEffect(() => {
    setStatus(initialStatus === 'ready' ? 'done' : initialStatus)
    if (initialError) setErrorMsg(initialError)
  }, [initialError, initialStatus])

  // 更新阶段状态
  const updatePhase = useCallback((key: string, updates: Partial<InstallPhase>) => {
    setPhases((prev) =>
      prev.map((p) => (p.key === key ? { ...p, ...updates } : p))
    )
  }, [])

  // 计算总进度
  const recalcOverall = useCallback((currentPhases: InstallPhase[]) => {
    const total = currentPhases.reduce((sum, p) => sum + p.percent, 0)
    setOverallPercent(Math.round(total / currentPhases.length))
  }, [])

  // 监听依赖进度事件
  useEffect(() => {
    const api = (window as any).electronAPI
    if (!api?.onDependencyProgress) return

    const unsubProgress = api.onDependencyProgress((evt: any) => {
      if (!evt || !evt.event) return

      switch (evt.event) {
        case 'start':
          if (evt.phase && evt.phase !== 'all') {
            updatePhase(evt.phase, { status: 'running', detail: evt.message })
          }
          break
        case 'progress':
          if (evt.phase && evt.phase !== 'all') {
            const percent = evt.percent ?? 0
            updatePhase(evt.phase, {
              status: 'running',
              percent,
              detail: evt.detail ?? `${Math.round(percent)}%`,
            })
            setPhases((prev) => {
              recalcOverall(prev)
              return prev
            })
          }
          break
        case 'done':
          if (evt.phase && evt.phase !== 'all') {
            updatePhase(evt.phase, { status: 'done', percent: 100 })
            setPhases((prev) => {
              recalcOverall(prev)
              return prev
            })
          }
          if (evt.phase === 'all') {
            setStatus('done')
            setOverallPercent(100)
            onReady()
          }
          break
        case 'error':
          if (evt.phase && evt.phase !== 'all') {
            updatePhase(evt.phase, { status: 'error', detail: evt.message })
          }
          setStatus('error')
          // 内层阶段的错误更具体；不要被随后发出的 all 阶段通用错误覆盖。
          setErrorMsg((previous) =>
            evt.phase === 'all' && previous ? previous : (evt.message ?? '安装失败')
          )
          break
      }
    })

    return () => {
      if (unsubProgress) unsubProgress()
    }
  }, [updatePhase, recalcOverall, onReady])

  // 初始依赖检测
  useEffect(() => {
    if (!autoCheck) return
    const api = (window as any).electronAPI
    if (!api?.checkDependencies) return

    let cancelled = false

    async function doCheck() {
      try {
        const res = await api.checkDependencies()
        if (cancelled) return

        if (res?.success && res.data) {
          setDepResult(res.data)
          if (res.data.all_ok) {
            // 所有依赖已就绪
            setStatus('done')
            setOverallPercent(100)
            onReady()
          } else {
            // 有缺失依赖，更新阶段状态
            setStatus('installing')
            setPhases((prev) =>
              prev.map((p) => {
                if (p.key === 'python_core') {
                  return { ...p, status: res.data.core_ok ? 'done' : 'pending', percent: res.data.core_ok ? 100 : 0 }
                }
                if (p.key === 'python_ai') {
                  return { ...p, status: res.data.ai_ok ? 'done' : 'pending', percent: res.data.ai_ok ? 100 : 0 }
                }
                if (p.key === 'ffmpeg') {
                  return { ...p, status: res.data.ffmpeg_ok ? 'done' : 'pending', percent: res.data.ffmpeg_ok ? 100 : 0 }
                }
                return p
              })
            )
          }
        } else {
          // 检测失败，直接进入主界面（后端会处理）
          setStatus('done')
          onReady()
        }
      } catch {
        if (cancelled) return
        setStatus('done')
        onReady()
      }
    }

    doCheck()
    return () => { cancelled = true }
  }, [autoCheck, onReady])

  // 手动重试安装
  const handleRetry = useCallback(async () => {
    const api = (window as any).electronAPI
    if (!api?.installDependencies) return

    setStatus('installing')
    setErrorMsg('')
    setPhases((prev) =>
      prev.map((p) => ({ ...p, status: 'pending', percent: 0, detail: undefined }))
    )
    setOverallPercent(0)

    try {
      const result = await api.installDependencies({ includeAi: true })
      if (result?.success === false) {
        setStatus('error')
        setErrorMsg(result.error || '依赖安装失败')
      }
    } catch {
      setStatus('error')
      setErrorMsg('启动安装失败')
    }
  }, [])

  return (
    <ConfigProvider
      theme={{
        algorithm: theme.darkAlgorithm,
        token: { colorPrimary: '#31B3AE' },
      }}
    >
      <div className="splash-screen">
        <div className="splash-content">
          {/* Logo 区域 */}
          <div className="splash-logo">
            <div className="logo-icon">LSC</div>
            <Title level={2} className="splash-title">
              直播切片系统
            </Title>
            <Text className="splash-subtitle">Live Stream Clipper</Text>
          </div>

          {/* 进度区域 */}
          <div className="splash-progress-area">
            {status === 'checking' && (
              <Text className="splash-status-text">正在检测运行环境...</Text>
            )}

            {status === 'installing' && (
              <>
                <div className="overall-progress">
                  <Progress
                    percent={overallPercent}
                    status="active"
                    strokeColor={{ from: '#31B3AE', to: '#2dd4bf' }}
                    format={(p) => <span className="progress-text">{p}%</span>}
                  />
                  <Text className="splash-status-text">
                    首次使用需要下载依赖，请耐心等待（约 1.5 GB）
                  </Text>
                </div>

                <div className="phase-list">
                  {phases.map((phase) => (
                    <div key={phase.key} className={`phase-item phase-${phase.status}`}>
                      <div className="phase-icon">
                        {phase.status === 'done' && <CheckCircleFilled style={{ color: '#34c759' }} />}
                        {phase.status === 'running' && <DownloadOutlined spin style={{ color: '#31B3AE' }} />}
                        {phase.status === 'pending' && <span className="phase-dot" />}
                        {phase.status === 'error' && <WarningOutlined style={{ color: '#ff3b30' }} />}
                      </div>
                      <div className="phase-info">
                        <Text className="phase-label">{phase.label}</Text>
                        {phase.detail && (
                          <Text className="phase-detail">{phase.detail}</Text>
                        )}
                      </div>
                      <div className="phase-percent">
                        {phase.status === 'running' && <Text>{Math.round(phase.percent)}%</Text>}
                        {phase.status === 'done' && <Text style={{ color: '#34c759' }}>完成</Text>}
                        {phase.status === 'pending' && <Text type="secondary">等待中</Text>}
                        {phase.status === 'error' && <Text type="danger">失败</Text>}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}

            {status === 'error' && (
              <div className="error-area">
                <WarningOutlined style={{ fontSize: 32, color: '#ff3b30', marginBottom: 12 }} />
                <div className="error-message" role="alert">{errorMsg}</div>
                <Space style={{ marginTop: 16 }}>
                  <Button
                    type="primary"
                    icon={<ReloadOutlined />}
                    onClick={handleRetry}
                  >
                    重试
                  </Button>
                  <Button onClick={onReady}>
                    跳过（部分功能不可用）
                  </Button>
                </Space>
              </div>
            )}

            {status === 'done' && (
              <div className="done-area">
                <CheckCircleFilled style={{ fontSize: 32, color: '#34c759' }} />
                <Text className="splash-status-text">环境就绪，正在启动...</Text>
              </div>
            )}
          </div>

          {/* 底部提示 */}
          <div className="splash-footer">
            <Text type="secondary" style={{ fontSize: 12 }}>
              依赖将安装至当前用户数据目录，不会污染系统环境
            </Text>
          </div>
        </div>
      </div>
    </ConfigProvider>
  )
}
