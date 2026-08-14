import { useEffect, useState, useRef } from 'react'
import { Button, message, Slider, Input, Select, Tooltip } from 'antd'
import { FolderOpenOutlined, ReloadOutlined, DownloadOutlined, FolderOutlined } from '@ant-design/icons'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useAppStore } from '@/store/appStore'
import LogViewer from '@/components/LogViewer'
import { RecordSettings, AppSettings } from '@/types'
import { EXPORT_PRESETS } from '@/services/exportPresets'
import { SettingsSection } from './SettingsSection'
import { SettingsRow } from './SettingsRow'
import { ToggleSwitch } from './ToggleSwitch'
import { DepStatus } from './DepStatus'
import './settings.css'

const SECTIONS = [
  { id: 'general', label: '通用' },
  { id: 'preview', label: '预览体验' },
  { id: 'env', label: '系统环境' },
  { id: 'recording', label: '录制与编码' },
  { id: 'ai', label: 'AI 分析' },
  { id: 'storage', label: '存储与草稿' },
  { id: 'account', label: '平台账号' },
  { id: 'shortcuts', label: '快捷键' },
  { id: 'about', label: '关于与更新' },
  { id: 'logs', label: '日志' },
] as const

const SELECT_W = { width: '100%', maxWidth: 220 }

function KeyBadge({ keys }: { keys: string[] }) {
  return (
    <span className="key-badge">
      {keys.map((key, i) => (
        <span key={i}>
          {i > 0 && ' + '}
          <kbd>{key}</kbd>
        </span>
      ))}
    </span>
  )
}

export default function Settings() {
  const { isConnected, send, on } = useWebSocket()
  const settings = useAppStore((state) => state.settings)
  const setSettings = useAppStore((state) => state.setSettings)
  const appSettings = useAppStore((state) => state.appSettings)
  const setAppSettings = useAppStore((state) => state.setAppSettings)
  const dependencyStatus = useAppStore((state) => state.dependencyStatus)
  const [checkingDeps, setCheckingDeps] = useState(false)
  const depCheckTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [appVersion, setAppVersion] = useState('')
  const [updateStatus, setUpdateStatus] = useState<{
    type: 'checking' | 'available' | 'not-available' | 'error'
    version?: string
    message?: string
    releaseUrl?: string
    releaseNotes?: string
  } | null>(null)
  const [douyinCookieText, setDouyinCookieText] = useState('')
  const [douyinCookieStatus, setDouyinCookieStatus] = useState<{
    configured?: boolean
    count?: number
    keys?: string[]
  } | null>(null)
  const [savingDouyinCookie, setSavingDouyinCookie] = useState(false)
  const [bilibiliCookieText, setBilibiliCookieText] = useState('')
  const [bilibiliCookieStatus, setBilibiliCookieStatus] = useState<{
    configured?: boolean
    count?: number
    keys?: string[]
  } | null>(null)
  const [savingBilibiliCookie, setSavingBilibiliCookie] = useState(false)
  const [huyaCookieText, setHuyaCookieText] = useState('')
  const [huyaCookieStatus, setHuyaCookieStatus] = useState<{
    configured?: boolean
    count?: number
    keys?: string[]
  } | null>(null)
  const [savingHuyaCookie, setSavingHuyaCookie] = useState(false)
  const [detectedJianyingDir, setDetectedJianyingDir] = useState('')

  useEffect(() => {
    window.electronAPI?.getAppVersion().then((v: string) => setAppVersion(v)).catch((e: unknown) => console.error('[Settings] getAppVersion failed:', e))
    window.electronAPI?.onUpdateStatus((status: any) => {
      setUpdateStatus(status)
    })
    return () => {
      window.electronAPI?.removeUpdateStatusListeners()
    }
  }, [])

  useEffect(() => {
    if (isConnected) {
      send('get_settings', {})
      setCheckingDeps(true)
      send('check_dependencies', {})
      send('get_douyin_cookie_status', {})
      send('get_bilibili_cookie_status', {})
      send('get_huya_cookie_status', {})
      send('get_jianying_draft_dir', {})
    }
  }, [isConnected, send])

  useEffect(() => {
    const unsubs = [
      on('get_douyin_cookie_status_response', (data: {
        success?: boolean
        configured?: boolean
        count?: number
        keys?: string[]
        error?: string
      }) => {
        if (data?.success === false && data.error) {
          message.error(`读取抖音 Cookie 状态失败：${data.error}`)
          return
        }
        setDouyinCookieStatus({
          configured: !!data?.configured,
          count: data?.count || 0,
          keys: data?.keys || [],
        })
      }),
      on('save_douyin_cookies_response', (data: {
        success?: boolean
        configured?: boolean
        count?: number
        keys?: string[]
        error?: string
      }) => {
        setSavingDouyinCookie(false)
        if (!data?.success) {
          message.error(data?.error || '保存抖音 Cookie 失败')
          return
        }
        setDouyinCookieStatus({
          configured: !!data.configured,
          count: data.count || 0,
          keys: data.keys || [],
        })
        setDouyinCookieText('')
        message.success(`抖音 Cookie 已保存（${data.count || 0} 项），请重新连接直播间`)
      }),
      on('get_bilibili_cookie_status_response', (data: {
        success?: boolean
        configured?: boolean
        count?: number
        keys?: string[]
        error?: string
      }) => {
        if (data?.success === false && data.error) {
          message.error(`读取 B站 Cookie 状态失败：${data.error}`)
          return
        }
        setBilibiliCookieStatus({
          configured: !!data?.configured,
          count: data?.count || 0,
          keys: data?.keys || [],
        })
      }),
      on('save_bilibili_cookies_response', (data: {
        success?: boolean
        configured?: boolean
        count?: number
        keys?: string[]
        error?: string
      }) => {
        setSavingBilibiliCookie(false)
        if (!data?.success) {
          message.error(data?.error || '保存 B站 Cookie 失败')
          return
        }
        setBilibiliCookieStatus({
          configured: !!data.configured,
          count: data.count || 0,
          keys: data.keys || [],
        })
        setBilibiliCookieText('')
        message.success(`B站 Cookie 已保存（${data.count || 0} 项），请重新连接直播间`)
      }),
      on('get_huya_cookie_status_response', (data: {
        success?: boolean
        configured?: boolean
        count?: number
        keys?: string[]
        error?: string
      }) => {
        if (data?.success === false && data.error) {
          message.error(`读取虎牙 Cookie 状态失败：${data.error}`)
          return
        }
        setHuyaCookieStatus({
          configured: !!data?.configured,
          count: data?.count || 0,
          keys: data?.keys || [],
        })
      }),
      on('save_huya_cookies_response', (data: {
        success?: boolean
        configured?: boolean
        count?: number
        keys?: string[]
        error?: string
      }) => {
        setSavingHuyaCookie(false)
        if (!data?.success) {
          message.error(data?.error || '保存虎牙 Cookie 失败')
          return
        }
        setHuyaCookieStatus({
          configured: !!data.configured,
          count: data.count || 0,
          keys: data.keys || [],
        })
        setHuyaCookieText('')
        message.success(`虎牙 Cookie 已保存（${data.count || 0} 项），请重新连接直播间`)
      }),
      on('get_jianying_draft_dir_response', (data: {
        success?: boolean
        draft_dir?: string
        auto_detected?: boolean
        exists?: boolean
        error?: string
      }) => {
        if (data?.success === false && data.error) {
          message.error(`读取剪映草稿目录失败：${data.error}`)
          return
        }
        setDetectedJianyingDir(data?.draft_dir || '')
      }),
      on('save_settings_response', (data: { success?: boolean; error?: string }) => {
        if (data?.success === false) {
          message.error(data.error || '保存设置失败')
        }
      }),
    ]
    return () => unsubs.forEach((u) => u())
  }, [on])

  const handleRecheckDeps = () => {
    if (!isConnected) return
    setCheckingDeps(true)
    send('check_dependencies', {})
    depCheckTimerRef.current = setTimeout(() => {
      depCheckTimerRef.current = null
      setCheckingDeps(false)
    }, 5000)
  }

  useEffect(() => {
    if (dependencyStatus) setCheckingDeps(false)
  }, [dependencyStatus])

  useEffect(() => {
    window.app?.getAutoLaunch().then((v) => setAppSettings({ autoLaunch: v })).catch((e: unknown) => console.error('[Settings] getAutoLaunch failed:', e))
    window.app?.getMinimizeToTray().then((v) => setAppSettings({ minimizeToTray: v })).catch((e: unknown) => console.error('[Settings] getMinimizeToTray failed:', e))
  }, [setAppSettings])

  // 保存模型统一：变更立即落盘（300ms 防抖合并连续改动，如 CRF 拖动）
  // 底部保存状态栏：pending → 防抖中；saved → 已写入（2.5s 后回到 clean）
  const [saveState, setSaveState] = useState<'clean' | 'pending' | 'saved'>('clean')
  const [savedAt, setSavedAt] = useState<string>('')
  const savedFlashTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const saveNow = () => {
    setSaveState('pending')
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(() => {
      saveTimerRef.current = null
      const st = useAppStore.getState()
      send('save_settings', { ...st.settings, appSettings: st.appSettings })
      setSaveState('saved')
      setSavedAt(new Date().toLocaleTimeString('zh-CN', { hour12: false }))
      if (savedFlashTimerRef.current) clearTimeout(savedFlashTimerRef.current)
      savedFlashTimerRef.current = setTimeout(() => setSaveState('clean'), 2500)
    }, 300)
  }

  useEffect(() => {
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
      if (savedFlashTimerRef.current) clearTimeout(savedFlashTimerRef.current)
      if (depCheckTimerRef.current) clearTimeout(depCheckTimerRef.current)
    }
  }, [])

  const handleRecordChange = <K extends keyof RecordSettings>(key: K, value: RecordSettings[K]) => {
    setSettings({ [key]: value })
    saveNow()
  }

  const handleAppSettingChange = <K extends keyof AppSettings>(key: K, value: AppSettings[K]) => {
    setAppSettings({ [key]: value })
    saveNow()
  }

  const handleThemeChange = (value: AppSettings['theme']) => {
    setAppSettings({ theme: value })
    if (value === 'dark') {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
    saveNow()
  }

  const handleAutoLaunchChange = (v: boolean) => {
    window.app?.setAutoLaunch(v)
    setAppSettings({ autoLaunch: v })
  }

  const handleMinimizeToTrayChange = (v: boolean) => {
    window.app?.setMinimizeToTray(v)
    setAppSettings({ minimizeToTray: v })
  }

  const handleBrowse = async () => {
    if (window.electronAPI) {
      const dir = await window.electronAPI.selectDirectory()
      if (dir) {
        handleRecordChange('output_dir', dir)
        message.success('存储路径已保存')
      }
    } else {
      message.info('请在 Electron 桌面版中使用目录选择功能')
    }
  }

  const handleBrowseJianyingDir = async () => {
    if (!window.electronAPI?.selectDirectory) {
      message.info('请在 Electron 桌面版中使用目录选择功能')
      return
    }
    const dir = await window.electronAPI.selectDirectory()
    if (!dir) return
    handleRecordChange('jianying_draft_dir', dir)
    message.success('剪映草稿目录已保存')
  }

  const handleResetJianyingDir = () => {
    handleRecordChange('jianying_draft_dir', '')
    send('get_jianying_draft_dir', {})
    message.success('已恢复自动探测剪映草稿目录')
  }

  const handleCheckUpdate = async () => {
    setUpdateStatus(null)
    const result = await window.electronAPI?.checkForUpdate()
    if (result && !result.success) {
      message.error(`检查更新失败: ${result.error}`)
    }
  }

  const handleDownloadUpdate = async () => {
    const result = await window.electronAPI?.downloadUpdate()
    if (result && !result.success) {
      message.error(`打开下载页失败: ${result.error}`)
    } else {
      message.info('已在浏览器中打开 GitHub Release 下载页')
    }
  }

  const handleSaveDouyinCookies = () => {
    if (!douyinCookieText.trim()) {
      message.warning('请先粘贴 Cookie 内容')
      return
    }
    setSavingDouyinCookie(true)
    send('save_douyin_cookies', { cookies: douyinCookieText })
  }

  const handleSaveBilibiliCookies = () => {
    if (!bilibiliCookieText.trim()) {
      message.warning('请先粘贴 Cookie 内容')
      return
    }
    setSavingBilibiliCookie(true)
    send('save_bilibili_cookies', { cookies: bilibiliCookieText })
  }

  const handleSaveHuyaCookies = () => {
    if (!huyaCookieText.trim()) {
      message.warning('请先粘贴 Cookie 内容')
      return
    }
    setSavingHuyaCookie(true)
    send('save_huya_cookies', { cookies: huyaCookieText })
  }

  const scrollToSection = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const handleOpenLogFolder = async () => {
    const result = await window.electronAPI?.openLogFolder?.()
    if (result && !result.success) {
      message.error(result.error || '无法打开日志目录')
    }
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: 16 }}>
      <div className="settings-layout">
        <nav className="settings-nav" aria-label="设置导航">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              type="button"
              className="settings-nav__item"
              onClick={() => scrollToSection(s.id)}
            >
              {s.label}
            </button>
          ))}
        </nav>

        <div className="settings-main">
          <SettingsSection id="general" title="通用">
            <SettingsRow label="主题">
              <Select
                size="small"
                style={SELECT_W}
                value={appSettings.theme}
                onChange={(v) => handleThemeChange(v as AppSettings['theme'])}
                options={[
                  { value: 'dark', label: '深色' },
                  { value: 'light', label: '浅色' },
                ]}
              />
            </SettingsRow>
            <SettingsRow label="开机自启">
              <ToggleSwitch checked={appSettings.autoLaunch} onChange={handleAutoLaunchChange} />
            </SettingsRow>
            <SettingsRow label="最小化到托盘">
              <ToggleSwitch checked={appSettings.minimizeToTray} onChange={handleMinimizeToTrayChange} />
            </SettingsRow>
          </SettingsSection>

          <div id="preview" className="settings-section">
            <div className="settings-section__title" style={{ marginBottom: 0 }}>
              <span style={{ whiteSpace: 'nowrap' }}>预览体验</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, textTransform: 'none', letterSpacing: 'normal' }}>
                <span style={{ fontSize: 13, fontWeight: 400, color: 'var(--text-50)', whiteSpace: 'nowrap', flexShrink: 0 }}>预览画质</span>
                <Select
                  size="small"
                  style={{ width: 150, maxWidth: '100%' }}
                  value={settings.preview_quality}
                  onChange={(v) => {
                    handleRecordChange('preview_quality', v)
                    message.warning('更改预览画质会重启预览，公共轴可能失效，请重新一键对齐', 4)
                  }}
                  options={[
                    { value: '原画', label: '原画（不缩放）' },
                    { value: '高清', label: '高清 720p' },
                    { value: '标清', label: '标清 480p' },
                    { value: '流畅', label: '流畅 360p' },
                  ]}
                />
              </div>
            </div>
          </div>

          <SettingsSection
            id="env"
            title="系统环境"
            extra={
              <Button
                type="text"
                size="small"
                icon={<ReloadOutlined spin={checkingDeps} />}
                onClick={handleRecheckDeps}
                disabled={!isConnected || checkingDeps}
              >
                重新检测
              </Button>
            }
          >
            <SettingsRow label="FFmpeg">
              <DepStatus
                ok={dependencyStatus?.ffmpeg.available}
                version={dependencyStatus?.ffmpeg.version}
                path={dependencyStatus?.ffmpeg.path}
              />
            </SettingsRow>
            <SettingsRow label="FFprobe">
              <DepStatus
                ok={dependencyStatus?.ffprobe.available}
                version={dependencyStatus?.ffprobe.version}
                path={dependencyStatus?.ffprobe.path}
              />
            </SettingsRow>
            <SettingsRow label="NVENC 硬件编码">
              <DepStatus
                ok={dependencyStatus?.nvenc.available}
                version={dependencyStatus?.nvenc.available ? 'h264_nvenc 可用' : '不可用'}
              />
            </SettingsRow>
            <SettingsRow label="Python">
              <DepStatus
                ok={dependencyStatus?.python.version ? true : undefined}
                version={dependencyStatus?.python.version}
                path={dependencyStatus?.python.path}
              />
            </SettingsRow>
          </SettingsSection>

          <SettingsSection id="recording" title="录制与编码">
            <SettingsRow label="默认画质">
              <Select
                size="small"
                style={SELECT_W}
                value={settings.quality}
                onChange={(v) => handleRecordChange('quality', v)}
                options={[
                  { value: '原画', label: '原画' },
                  { value: '蓝光', label: '蓝光' },
                  { value: '超清', label: '超清' },
                  { value: '高清', label: '高清' },
                  { value: '流畅', label: '流畅' },
                ]}
              />
            </SettingsRow>
            <SettingsRow label="默认编码器">
              <Select
                size="small"
                style={SELECT_W}
                value={settings.encoder}
                onChange={(v) => handleRecordChange('encoder', v)}
                options={[
                  { value: 'h264_nvenc', label: 'h264_nvenc (NVIDIA，推荐)' },
                  { value: 'hevc_nvenc', label: 'hevc_nvenc (NVIDIA)' },
                  { value: 'h264_qsv', label: 'h264_qsv (Intel)' },
                  { value: 'h264_amf', label: 'h264_amf (AMD)' },
                  { value: 'copy', label: 'copy（直拷，最省）' },
                  { value: 'libx264', label: 'libx264（CPU）' },
                  { value: 'libx265', label: 'libx265（CPU）' },
                ]}
              />
            </SettingsRow>
            <SettingsRow label="编码参数">
              <Select
                size="small"
                style={SELECT_W}
                value={settings.param_mode}
                onChange={(v) => handleRecordChange('param_mode', v)}
                options={[
                  { value: 'CRF 质量', label: 'CRF 质量' },
                  { value: '自定义码率', label: '自定义码率' },
                  { value: '不限制', label: '不限制' },
                ]}
              />
            </SettingsRow>
            <SettingsRow label="编码预设">
              <Select
                size="small"
                style={SELECT_W}
                value={settings.preset || 'medium'}
                onChange={(v) => handleRecordChange('preset', v)}
                options={[
                  { value: 'ultrafast', label: 'ultrafast（最快）' },
                  { value: 'fast', label: 'fast（快速）' },
                  { value: 'medium', label: 'medium（均衡）' },
                  { value: 'slow', label: 'slow（慢速）' },
                ]}
              />
            </SettingsRow>
            {settings.param_mode === '自定义码率' && settings.encoder !== 'copy' && (
              <SettingsRow label="码率">
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <Select
                    size="small"
                    style={{ width: 90 }}
                    value={settings.bitrate_unit}
                    onChange={(v) => handleRecordChange('bitrate_unit', v)}
                    options={[
                      { value: 'kbps', label: 'kbps' },
                      { value: 'Mbps', label: 'Mbps' },
                    ]}
                  />
                  <Select
                    size="small"
                    style={{ width: 120 }}
                    value={String(settings.bitrate)}
                    onChange={(v) => handleRecordChange('bitrate', v)}
                    options={[1000, 2000, 4000, 6000, 8000, 10000, 12000, 15000, 20000].map((b) => ({
                      value: String(b),
                      label: String(b),
                    }))}
                  />
                </div>
              </SettingsRow>
            )}
            {settings.param_mode === 'CRF 质量' && settings.encoder !== 'copy' && (
              <SettingsRow label="CRF">
                <div style={{ width: '100%', maxWidth: 320, padding: '0 4px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text-tertiary)' }}>
                    <span>高质量</span>
                    <span style={{ fontWeight: 600, color: 'var(--brand-400)' }}>当前 CRF：{settings.crf}</span>
                    <span>小体积</span>
                  </div>
                  <Slider
                    min={18}
                    max={28}
                    value={settings.crf}
                    onChange={(v) => handleRecordChange('crf', v)}
                    marks={{ 18: '18', 23: '推荐', 28: '28' }}
                    tooltip={{ formatter: (v) => `CRF ${v}` }}
                    style={{ width: '100%', margin: '4px 0' }}
                  />
                </div>
              </SettingsRow>
            )}
            <SettingsRow label="录制分辨率">
              <Select
                size="small"
                style={SELECT_W}
                value={settings.resolution}
                onChange={(v) => handleRecordChange('resolution', v)}
                options={[
                  { value: '原画', label: '原画' },
                  { value: '1920:1080', label: '1080p (1920x1080)' },
                  { value: '1280:720', label: '720p (1280x720)' },
                  { value: '854:480', label: '480p (854x480)' },
                ]}
              />
            </SettingsRow>
            <SettingsRow label="录制帧率">
              <Select
                size="small"
                style={SELECT_W}
                value={settings.framerate}
                onChange={(v) => handleRecordChange('framerate', v)}
                options={[
                  { value: '原画', label: '原画' },
                  { value: '60', label: '60 fps' },
                  { value: '30', label: '30 fps' },
                  { value: '24', label: '24 fps' },
                ]}
              />
            </SettingsRow>
            <SettingsRow label="音频编码">
              <Select
                size="small"
                style={SELECT_W}
                value={settings.audio_bitrate}
                onChange={(v) => handleRecordChange('audio_bitrate', v)}
                options={[
                  { value: '128k', label: 'AAC 128k' },
                  { value: '192k', label: 'AAC 192k' },
                  { value: '256k', label: 'AAC 256k' },
                ]}
              />
            </SettingsRow>
          </SettingsSection>

          <SettingsSection id="ai" title="AI 分析">
            <SettingsRow label="OCR 加速">
              <Select
                size="small"
                style={SELECT_W}
                value={settings.ocr_accel || 'dml'}
                onChange={(v) => {
                  handleRecordChange('ocr_accel', v as RecordSettings['ocr_accel'])
                  message.success('OCR 加速已保存（下次识别生效）', 2)
                }}
                options={[
                  { value: 'dml', label: 'DirectML（Windows GPU，推荐）' },
                  { value: 'auto', label: '自动' },
                  { value: 'cuda', label: 'CUDA（NVIDIA）' },
                  { value: 'cpu', label: '仅 CPU' },
                ]}
              />
            </SettingsRow>
            <SettingsRow label="共享进样">
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <span style={{ color: 'var(--text-40)', fontSize: 12 }}>
                  预览与录制共用同一进程，录制中断会导致预览短暂重连
                </span>
                <ToggleSwitch
                  checked={!!settings.shared_ingest_enabled}
                  onChange={(v) => {
                    handleRecordChange('shared_ingest_enabled', v)
                    message.success(v ? '已开启共享进样（新预览/录制生效）' : '已关闭共享进样（新预览/录制生效）', 2)
                  }}
                />
              </div>
            </SettingsRow>
            <SettingsRow label="并发导出数">
              <Select
                size="small"
                style={SELECT_W}
                value={settings.export_max_concurrent ?? 2}
                onChange={(v) => {
                  handleRecordChange('export_max_concurrent', Number(v))
                  message.success(Number(v) === 1 ? '已设为单路导出（降低 CPU 负载）' : '已设为双路并发导出', 2)
                }}
                options={[
                  { value: 2, label: '2 路（默认）' },
                  { value: 1, label: '1 路（低负载）' },
                ]}
              />
            </SettingsRow>
            <SettingsRow label="默认导出预设">
              <Tooltip title={(() => {
                const preset = EXPORT_PRESETS.find(p => p.id === (appSettings.default_export_preset || 'douyin_vertical'))
                return preset ? `${preset.name} — ${preset.description}` : ''
              })()}>
                <Select
                  size="small"
                  style={{ width: 'min(100%, 280px)' }}
                  value={appSettings.default_export_preset || 'douyin_vertical'}
                  onChange={(v) => handleAppSettingChange('default_export_preset', v)}
                  options={EXPORT_PRESETS.map((p) => ({
                    value: p.id,
                    label: `${p.name} — ${p.description}`,
                  }))}
                />
              </Tooltip>
            </SettingsRow>
          </SettingsSection>

          <SettingsSection id="storage" title="存储与草稿">
            <SettingsRow label="存储路径">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', maxWidth: 360 }}>
                <span className="settings-path">{settings.output_dir}</span>
                <button type="button" onClick={() => { void handleBrowse() }} className="browse-btn">
                  <FolderOpenOutlined style={{ fontSize: 14 }} />
                  浏览
                </button>
              </div>
            </SettingsRow>
            <SettingsRow label="剪映草稿目录">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, width: '100%', maxWidth: 360 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%' }}>
                  <span
                    className="settings-path"
                    style={{ color: settings.jianying_draft_dir ? 'var(--text-300)' : 'var(--text-tertiary)' }}
                  >
                    {settings.jianying_draft_dir
                      || detectedJianyingDir
                      || '未检测到剪映，请手动选择'}
                  </span>
                  <button type="button" onClick={() => { void handleBrowseJianyingDir() }} className="browse-btn">
                    更改
                  </button>
                  <button type="button" onClick={handleResetJianyingDir} className="browse-btn">
                    恢复自动探测
                  </button>
                </div>
                {!settings.jianying_draft_dir && !detectedJianyingDir && (
                  <span style={{ fontSize: 11, color: 'var(--state-warning)', lineHeight: 1.5 }}>
                    自动探测失败：请安装剪映专业版或手动指定草稿目录
                  </span>
                )}
              </div>
            </SettingsRow>
          </SettingsSection>

          <SettingsSection
            id="account"
            title="平台账号"
            bodyStyle={{ padding: 0, background: 'transparent', overflow: 'visible' }}
          >
            <div style={{
              background: 'var(--background-800)',
              borderRadius: 'var(--radius)',
              overflow: 'hidden',
              padding: 16,
              marginBottom: 16,
            }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-50)', marginBottom: 8 }}>抖音 Cookie</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, marginBottom: 10, color: douyinCookieStatus?.configured ? 'var(--state-success)' : 'var(--state-warning)' }}>
                {douyinCookieStatus?.configured
                  ? <>
                      <span>已配置 {douyinCookieStatus.count || 0} 项</span>
                      {(douyinCookieStatus.keys || []).length > 0 && (
                        <Tooltip title={(douyinCookieStatus.keys || []).join(', ')}>
                          <span style={{
                            fontSize: 11,
                            color: 'var(--text-tertiary)',
                            textDecoration: 'underline dotted',
                            cursor: 'help',
                          }}>
                            查看键名
                          </span>
                        </Tooltip>
                      )}
                    </>
                  : '尚未配置有效 Cookie'}
              </div>
              <Input.TextArea
                value={douyinCookieText}
                onChange={(e) => setDouyinCookieText(e.target.value)}
                placeholder='支持 JSON 对象/数组，或 ttwid=...; sessionid=... 格式'
                autoSize={{ minRows: 4, maxRows: 10 }}
                style={{ marginBottom: 10 }}
              />
              <Button
                type="primary"
                size="small"
                loading={savingDouyinCookie}
                onClick={handleSaveDouyinCookies}
                disabled={!isConnected}
              >
                保存抖音 Cookie
              </Button>
            </div>

            <div style={{
              background: 'var(--background-800)',
              borderRadius: 'var(--radius)',
              overflow: 'hidden',
              padding: 16,
              marginBottom: 16,
            }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-50)', marginBottom: 8 }}>B站 Cookie</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, marginBottom: 10, color: bilibiliCookieStatus?.configured ? 'var(--state-success)' : 'var(--state-warning)' }}>
                {bilibiliCookieStatus?.configured
                  ? <>
                      <span>已配置 {bilibiliCookieStatus.count || 0} 项</span>
                      {(bilibiliCookieStatus.keys || []).length > 0 && (
                        <Tooltip title={(bilibiliCookieStatus.keys || []).join(', ')}>
                          <span style={{
                            fontSize: 11,
                            color: 'var(--text-tertiary)',
                            textDecoration: 'underline dotted',
                            cursor: 'help',
                          }}>
                            查看键名
                          </span>
                        </Tooltip>
                      )}
                    </>
                  : '尚未配置有效 Cookie'}
              </div>
              <Input.TextArea
                value={bilibiliCookieText}
                onChange={(e) => setBilibiliCookieText(e.target.value)}
                placeholder='支持 JSON 对象/数组，或 SESSDATA=...; bili_jct=... 格式'
                autoSize={{ minRows: 4, maxRows: 10 }}
                style={{ marginBottom: 10 }}
              />
              <Button
                type="primary"
                size="small"
                loading={savingBilibiliCookie}
                onClick={handleSaveBilibiliCookies}
                disabled={!isConnected}
              >
                保存 B站 Cookie
              </Button>
            </div>

            <div style={{
              background: 'var(--background-800)',
              borderRadius: 'var(--radius)',
              overflow: 'hidden',
              padding: 16,
            }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-50)', marginBottom: 8 }}>虎牙 Cookie</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, marginBottom: 10, color: huyaCookieStatus?.configured ? 'var(--state-success)' : 'var(--state-warning)' }}>
                {huyaCookieStatus?.configured
                  ? <>
                      <span>已配置 {huyaCookieStatus.count || 0} 项</span>
                      {(huyaCookieStatus.keys || []).length > 0 && (
                        <Tooltip title={(huyaCookieStatus.keys || []).join(', ')}>
                          <span style={{
                            fontSize: 11,
                            color: 'var(--text-tertiary)',
                            textDecoration: 'underline dotted',
                            cursor: 'help',
                          }}>
                            查看键名
                          </span>
                        </Tooltip>
                      )}
                    </>
                  : '尚未配置有效 Cookie'}
              </div>
              <Input.TextArea
                value={huyaCookieText}
                onChange={(e) => setHuyaCookieText(e.target.value)}
                placeholder='支持 JSON 对象/数组，或 udb_uid=...; udb_guid=... 格式'
                autoSize={{ minRows: 4, maxRows: 10 }}
                style={{ marginBottom: 10 }}
              />
              <Button
                type="primary"
                size="small"
                loading={savingHuyaCookie}
                onClick={handleSaveHuyaCookies}
                disabled={!isConnected}
              >
                保存虎牙 Cookie
              </Button>
            </div>
          </SettingsSection>

          <SettingsSection id="shortcuts" title="快捷键">
            <SettingsRow label="页面：工作台"><KeyBadge keys={['Ctrl', '1']} /></SettingsRow>
            <SettingsRow label="页面：设置"><KeyBadge keys={['Ctrl', '2']} /></SettingsRow>
            <SettingsRow label="刷新页面"><KeyBadge keys={['F5']} /></SettingsRow>
            <SettingsRow label="播放/暂停"><KeyBadge keys={['Space']} /></SettingsRow>
            <SettingsRow label="标记入点"><KeyBadge keys={['I']} /></SettingsRow>
            <SettingsRow label="标记出点"><KeyBadge keys={['O']} /></SettingsRow>
            <SettingsRow label="切换录制"><KeyBadge keys={['R']} /></SettingsRow>
            <SettingsRow label="静音/取消静音"><KeyBadge keys={['M']} /></SettingsRow>
            <SettingsRow label="放大预览"><KeyBadge keys={['F']} /></SettingsRow>
            <SettingsRow label="批量开始录制"><KeyBadge keys={['Ctrl', 'R']} /></SettingsRow>
            <SettingsRow label="批量停止录制"><KeyBadge keys={['Ctrl', 'Shift', 'R']} /></SettingsRow>
            <SettingsRow label="全选房间"><KeyBadge keys={['Ctrl', 'Shift', 'A']} /></SettingsRow>
            <SettingsRow label="导出切片"><KeyBadge keys={['Ctrl', 'E']} /></SettingsRow>
          </SettingsSection>

          <SettingsSection id="about" title="关于与更新">
            <SettingsRow label="版本">
              <span style={{ fontSize: 13, color: 'var(--text-400)' }}>v{appVersion || '1.0.0'}</span>
            </SettingsRow>
            <SettingsRow label="">
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
                {updateStatus?.type === 'checking' && (
                  <span style={{ fontSize: 12, color: 'var(--text-400)' }}>正在检查更新...</span>
                )}
                {updateStatus?.type === 'not-available' && (
                  <span style={{ fontSize: 12, color: 'var(--state-success)' }}>
                    ✓ 已是最新版本 v{updateStatus.version}
                  </span>
                )}
                {updateStatus?.type === 'available' && (
                  <>
                    <span style={{ fontSize: 12, color: 'var(--brand-400)', fontWeight: 500 }}>
                      发现新版本 v{updateStatus.version}
                    </span>
                    {updateStatus.releaseNotes && (
                      <span style={{
                        fontSize: 11,
                        color: 'var(--text-400)',
                        maxWidth: 220,
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                        lineHeight: 1.5,
                      }}>
                        {String(updateStatus.releaseNotes).slice(0, 120)}{String(updateStatus.releaseNotes).length > 120 ? '...' : ''}
                      </span>
                    )}
                    <Button
                      type="primary"
                      size="small"
                      icon={<DownloadOutlined />}
                      onClick={() => { void handleDownloadUpdate() }}
                    >
                      前往下载
                    </Button>
                  </>
                )}
                {updateStatus?.type === 'error' && (
                  <span style={{ fontSize: 12, color: 'var(--state-error)' }}>
                    {updateStatus.message || '更新失败'}
                  </span>
                )}
                <Button onClick={() => { void handleCheckUpdate() }} loading={updateStatus?.type === 'checking'}>
                  检查更新
                </Button>
              </div>
            </SettingsRow>
          </SettingsSection>

          <SettingsSection
            id="logs"
            title="日志"
            extra={
              <Button
                type="text"
                size="small"
                icon={<FolderOutlined />}
                onClick={() => { void handleOpenLogFolder() }}
              >
                打开日志目录
              </Button>
            }
            bodyStyle={{ padding: 16 }}
          >
            <LogViewer />
          </SettingsSection>
        </div>
      </div>
      </div>
      {/* 底部保存状态栏（保留自动保存语义：变更即落盘，此处仅反馈状态） */}
      <div style={{
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '10px 18px',
        borderTop: '1px solid var(--border-default)',
        background: 'var(--bg-secondary)',
      }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 12, color: 'var(--text-tertiary)' }}>
          <span style={{
            width: 7,
            height: 7,
            borderRadius: '50%',
            background: saveState === 'pending' ? 'var(--state-warning-dark)' : 'var(--state-success)',
            boxShadow: `0 0 6px ${saveState === 'pending' ? 'var(--state-warning-dark)' : 'var(--state-success)'}`,
          }} />
          {saveState === 'pending' ? '正在保存…' : saveState === 'saved' ? `已自动保存 · ${savedAt}` : '全部已保存'}
        </span>
        <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>更改即时生效</span>
      </div>
    </div>
  )
}
