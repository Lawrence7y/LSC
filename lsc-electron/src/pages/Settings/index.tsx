import { useEffect, useState, useRef } from 'react'
import type { ReactNode } from 'react'
import { Button, App, Slider, Input, Select, Tooltip } from 'antd'
import {
  FolderOpenOutlined,
  ReloadOutlined,
  DownloadOutlined,
  FolderOutlined,
  SettingOutlined,
  DesktopOutlined,
  ToolOutlined,
  VideoCameraOutlined,
  ThunderboltOutlined,
  UserOutlined,
  KeyOutlined,
  InfoCircleOutlined,
  FileTextOutlined,
  DragOutlined,
} from '@ant-design/icons'
import { useWebSocket } from '@/hooks/useWebSocket'
import { SHORTCUT_DOCS, MOUSE_DOCS } from '@/hooks/useKeyboardShortcuts'
import { useAppStore } from '@/store/appStore'
import LogViewer from '@/components/LogViewer'
import { useI18n, LOCALES, type I18nT } from '@/i18n'
import { RecordSettings, AppSettings } from '@/types'
import { EXPORT_PRESETS } from '@/services/exportPresets'
import { SettingsSection } from './SettingsSection'
import { SettingsRow } from './SettingsRow'
import { ToggleSwitch } from './ToggleSwitch'
import { DepStatus } from './DepStatus'
import {
  DEFAULT_TIMELINE_REPLAY_SECONDS,
  REPLAY_BUFFER_OPTIONS,
} from '@/utils/replaySettings'
import './settings.css'

function getSections(t: I18nT): { id: string; label: string; icon?: ReactNode }[] {
  return [
    { id: 'general', label: t('通用偏好'), icon: <SettingOutlined /> },
    { id: 'workbench', label: t('监看与工作台'), icon: <DesktopOutlined /> },
    { id: 'env', label: t('系统环境诊断'), icon: <ToolOutlined /> },
    { id: 'recording', label: t('录制与编码'), icon: <VideoCameraOutlined /> },
    { id: 'ai', label: t('AI 智能分析'), icon: <ThunderboltOutlined /> },
    { id: 'storage', label: t('存储与草稿'), icon: <FolderOpenOutlined /> },
    { id: 'account', label: t('平台凭证 Cookie'), icon: <UserOutlined /> },
    { id: 'shortcuts', label: t('快捷键与鼠标操作'), icon: <KeyOutlined /> },
    { id: 'about', label: t('关于与更新'), icon: <InfoCircleOutlined /> },
    { id: 'logs', label: t('运行日志'), icon: <FileTextOutlined /> },
  ]
}

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

/** 鼠标手势位：复用 kbd 的视觉语言，但不伪装成按键 */
function MouseBadge({ label }: { label: string }) {
  return (
    <span className="key-badge">
      <DragOutlined style={{ color: 'var(--text-tertiary)', fontSize: 12 }} />
      <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{label}</span>
    </span>
  )
}

export default function Settings() {
  const { t, locale, setLocale } = useI18n()
  // context 版 message：静态调用在 antd v5 下不跟随 ConfigProvider 主题
  const { message } = App.useApp()
  const SECTIONS = getSections(t)
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
  const [activeSection, setActiveSection] = useState('general')

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
          message.error(t('读取抖音 Cookie 状态失败：{error}', { error: data.error }))
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
          message.error(data?.error || t('保存抖音 Cookie 失败'))
          return
        }
        setDouyinCookieStatus({
          configured: !!data.configured,
          count: data.count || 0,
          keys: data.keys || [],
        })
        setDouyinCookieText('')
        message.success(t('抖音 Cookie 已保存（{count} 项），请重新连接直播间', { count: data.count || 0 }))
      }),
      on('get_bilibili_cookie_status_response', (data: {
        success?: boolean
        configured?: boolean
        count?: number
        keys?: string[]
        error?: string
      }) => {
        if (data?.success === false && data.error) {
          message.error(t('读取 B站 Cookie 状态失败：{error}', { error: data.error }))
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
          message.error(data?.error || t('保存 B站 Cookie 失败'))
          return
        }
        setBilibiliCookieStatus({
          configured: !!data.configured,
          count: data.count || 0,
          keys: data.keys || [],
        })
        setBilibiliCookieText('')
        message.success(t('B站 Cookie 已保存（{count} 项），请重新连接直播间', { count: data.count || 0 }))
      }),
      on('get_huya_cookie_status_response', (data: {
        success?: boolean
        configured?: boolean
        count?: number
        keys?: string[]
        error?: string
      }) => {
        if (data?.success === false && data.error) {
          message.error(t('读取虎牙 Cookie 状态失败：{error}', { error: data.error }))
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
          message.error(data?.error || t('保存虎牙 Cookie 失败'))
          return
        }
        setHuyaCookieStatus({
          configured: !!data.configured,
          count: data.count || 0,
          keys: data.keys || [],
        })
        setHuyaCookieText('')
        message.success(t('虎牙 Cookie 已保存（{count} 项），请重新连接直播间', { count: data.count || 0 }))
      }),
      on('get_jianying_draft_dir_response', (data: {
        success?: boolean
        draft_dir?: string
        auto_detected?: boolean
        exists?: boolean
        error?: string
      }) => {
        if (data?.success === false && data.error) {
          message.error(t('读取剪映草稿目录失败：{error}', { error: data.error }))
          return
        }
        setDetectedJianyingDir(data?.draft_dir || '')
      }),
      on('save_settings_response', (data: { success?: boolean; error?: string }) => {
        if (data?.success === false) {
          message.error(data.error || t('保存设置失败'))
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
        message.success(t('存储路径已保存'))
      }
    } else {
      message.info(t('请在 Electron 桌面版中使用目录选择功能'))
    }
  }

  const handleBrowseJianyingDir = async () => {
    if (!window.electronAPI?.selectDirectory) {
      message.info(t('请在 Electron 桌面版中使用目录选择功能'))
      return
    }
    const dir = await window.electronAPI.selectDirectory()
    if (!dir) return
    handleRecordChange('jianying_draft_dir', dir)
    message.success(t('剪映草稿目录已保存'))
  }

  const handleResetJianyingDir = () => {
    handleRecordChange('jianying_draft_dir', '')
    send('get_jianying_draft_dir', {})
    message.success(t('已恢复自动探测剪映草稿目录'))
  }

  const handleCheckUpdate = async () => {
    setUpdateStatus(null)
    const result = await window.electronAPI?.checkForUpdate()
    if (result && !result.success) {
      message.error(t('检查更新失败: {error}', { error: result.error ?? '' }))
    }
  }

  const handleDownloadUpdate = async () => {
    const result = await window.electronAPI?.downloadUpdate()
    if (result && !result.success) {
      message.error(t('打开下载页失败: {error}', { error: result.error ?? '' }))
    } else {
      message.info(t('已在浏览器中打开 GitHub Release 下载页'))
    }
  }

  const handleSaveDouyinCookies = () => {
    if (!douyinCookieText.trim()) {
      message.warning(t('请先粘贴 Cookie 内容'))
      return
    }
    setSavingDouyinCookie(true)
    send('save_douyin_cookies', { cookies: douyinCookieText })
  }

  const handleSaveBilibiliCookies = () => {
    if (!bilibiliCookieText.trim()) {
      message.warning(t('请先粘贴 Cookie 内容'))
      return
    }
    setSavingBilibiliCookie(true)
    send('save_bilibili_cookies', { cookies: bilibiliCookieText })
  }

  const handleSaveHuyaCookies = () => {
    if (!huyaCookieText.trim()) {
      message.warning(t('请先粘贴 Cookie 内容'))
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
      message.error(result.error || t('无法打开日志目录'))
    }
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: 16 }}>
      <div className="settings-layout">
        <nav className="settings-nav" aria-label={t('设置导航')}>
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              type="button"
              className={`settings-nav__item${activeSection === s.id ? ' is-active' : ''}`}
              onClick={() => {
                setActiveSection(s.id)
                scrollToSection(s.id)
              }}
            >
              {s.icon}
              <span>{s.label}</span>
            </button>
          ))}
        </nav>

        <div className="settings-main">
          <SettingsSection
            id="general"
            title={t('通用偏好')}
            description={t('配置界面基础语言、外观主题与启动行为')}
          >
            <SettingsRow
              label={t('界面语言')}
              description={t('支持简体中文与 English 即时热切换')}
            >
              <Select
                size="small"
                style={SELECT_W}
                value={locale}
                onChange={(v) => setLocale(v)}
                options={LOCALES}
              />
            </SettingsRow>
            <SettingsRow
              label={t('色彩外观')}
              description={t('素雅矿物青与高对比石板灰，长时间监看更护眼')}
            >
              <Select
                size="small"
                style={SELECT_W}
                value={appSettings.theme}
                onChange={(v) => handleThemeChange(v as AppSettings['theme'])}
                options={[
                  { value: 'dark', label: t('深色模式') },
                  { value: 'light', label: t('浅色模式') },
                ]}
              />
            </SettingsRow>
            <SettingsRow
              label={t('开机自启')}
              description={t('启动计算机时在后台自动拉起客户端')}
            >
              <ToggleSwitch checked={appSettings.autoLaunch} onChange={handleAutoLaunchChange} />
            </SettingsRow>
            <SettingsRow
              label={t('最小化到托盘')}
              description={t('关闭窗口时最小化至托盘，保证后台录制不中断')}
            >
              <ToggleSwitch checked={appSettings.minimizeToTray} onChange={handleMinimizeToTrayChange} />
            </SettingsRow>
          </SettingsSection>

          <SettingsSection
            id="workbench"
            title={t('监看与工作台')}
            description={t('控制多路画面排布、网格密度与时间戳对齐策略')}
          >
            <SettingsRow
              label={t('工作台默认视图')}
              description={t('紧凑模式收拢次要浮层，6 路画面 100% 满屏无纵向滚动条')}
            >
              <Select
                size="small"
                style={SELECT_W}
                value={appSettings.workbenchViewMode || 'compact'}
                onChange={(v) => handleAppSettingChange('workbenchViewMode', v)}
                options={[
                  { value: 'compact', label: t('紧凑无滚动模式 (推荐)') },
                  { value: 'standard', label: t('标准网格模式') },
                ]}
              />
            </SettingsRow>
            <SettingsRow
              label={t('开播自动时间戳对齐')}
              description={t('多路流建立连接后，自动以首个关键帧对齐播放头')}
            >
              <ToggleSwitch
                checked={appSettings.autoAlignOnLive ?? true}
                onChange={(v) => handleAppSettingChange('autoAlignOnLive', v)}
              />
            </SettingsRow>
            <SettingsRow
              label={t('多流监看默认静音全部')}
              description={t('防止 6 路音频同时回放产生混杂刺耳爆音')}
            >
              <ToggleSwitch
                checked={appSettings.defaultMuteAll ?? true}
                onChange={(v) => handleAppSettingChange('defaultMuteAll', v)}
              />
            </SettingsRow>
            <SettingsRow
              label={t('预览画质')}
              description={t('更改画质将重启预览通道，降低画质可减轻多流解码显存')}
            >
              <Select
                size="small"
                style={SELECT_W}
                value={settings.preview_quality}
                onChange={(v) => {
                  handleRecordChange('preview_quality', v)
                  message.warning(t('更改预览画质会重启预览，公共轴可能失效，请重新一键对齐'), 4)
                }}
                options={[
                  { value: '原画', label: t('原画（不缩放）') },
                  { value: '高清', label: t('高清 720p') },
                  { value: '标清', label: t('标清 480p') },
                  { value: '流畅', label: t('流畅 360p') },
                ]}
              />
            </SettingsRow>
            <SettingsRow
              label={t('时间线回放')}
              description={t('选择直播沿前可回看的时长；关闭可降低 Electron 内存占用，重新开启后从新收到的分片开始积累')}
            >
              <Select
                size="small"
                style={SELECT_W}
                value={settings.timeline_replay_seconds ?? DEFAULT_TIMELINE_REPLAY_SECONDS}
                onChange={(v) => {
                  const seconds = Number(v)
                  handleRecordChange('timeline_replay_seconds', seconds)
                  message.success(t(
                    seconds === 0 ? '已关闭时间线回放（仅保留播放缓存）' : '时间线回放已设为 {time}',
                    { time: seconds >= 60 ? `${seconds / 60} 分钟` : `${seconds} 秒` },
                  ), 2)
                }}
                options={[
                  { value: REPLAY_BUFFER_OPTIONS[0], label: t('关闭回放（最低占用）') },
                  { value: REPLAY_BUFFER_OPTIONS[1], label: t('2 分钟') },
                  { value: REPLAY_BUFFER_OPTIONS[2], label: t('5 分钟（推荐）') },
                  { value: REPLAY_BUFFER_OPTIONS[3], label: t('10 分钟（高内存）') },
                ]}
              />
            </SettingsRow>
          </SettingsSection>

          <SettingsSection
            id="env"
            title={t('系统环境诊断')}
            description={t('底层视频推流、转码与通信管道健康状态诊断')}
            extra={
              <Button
                type="text"
                size="small"
                icon={<ReloadOutlined spin={checkingDeps} />}
                onClick={handleRecheckDeps}
                disabled={!isConnected || checkingDeps}
              >
                {t('重新检测')}
              </Button>
            }
          >
            <SettingsRow
              label={t('FFmpeg 转码引擎')}
              description={t('视频切片流提取与封装核心组件')}
            >
              <DepStatus
                ok={dependencyStatus?.ffmpeg.available}
                version={dependencyStatus?.ffmpeg.version}
                path={dependencyStatus?.ffmpeg.path}
              />
            </SettingsRow>
            <SettingsRow
              label={t('FFprobe 媒体探针')}
              description={t('媒体元数据分析与时间戳探查工具')}
            >
              <DepStatus
                ok={dependencyStatus?.ffprobe.available}
                version={dependencyStatus?.ffprobe.version}
                path={dependencyStatus?.ffprobe.path}
              />
            </SettingsRow>
            <SettingsRow
              label={t('NVENC 硬件加速')}
              description={t('NVIDIA 独立显卡 GPU 视频硬件编码支持')}
            >
              <DepStatus
                ok={dependencyStatus?.nvenc.available}
                version={dependencyStatus?.nvenc.available ? t('h264_nvenc 可用') : t('不可用')}
              />
            </SettingsRow>
            <SettingsRow
              label={t('Python 运行时')}
              description={t('本地高并发录制分析后台守护服务')}
            >
              <DepStatus
                ok={dependencyStatus?.python.version ? true : undefined}
                version={dependencyStatus?.python.version}
                path={dependencyStatus?.python.path}
              />
            </SettingsRow>
          </SettingsSection>

          <SettingsSection
            id="recording"
            title={t('录制与编码')}
            description={t('调优 GPU 硬件加速编解码器与视频画质参数')}
          >
            <SettingsRow
              label={t('默认画质')}
              description={t('多路录制的基础输出分辨率档位')}
            >
              <Select
                size="small"
                style={SELECT_W}
                value={settings.quality}
                onChange={(v) => handleRecordChange('quality', v)}
                options={[
                  { value: '原画', label: t('原画') },
                  { value: '蓝光', label: t('蓝光') },
                  { value: '超清', label: t('超清') },
                  { value: '高清', label: t('高清') },
                  { value: '流畅', label: t('流畅') },
                ]}
              />
            </SettingsRow>
            <SettingsRow
              label={t('默认编码器')}
              description={t('优先选用 GPU 硬件加速，可大幅降低系统 CPU 占用')}
            >
              <Select
                size="small"
                style={SELECT_W}
                value={settings.encoder}
                onChange={(v) => handleRecordChange('encoder', v)}
                options={[
                  { value: 'h264_nvenc', label: t('h264_nvenc (NVIDIA，推荐)') },
                  { value: 'hevc_nvenc', label: t('hevc_nvenc (NVIDIA)') },
                  { value: 'h264_qsv', label: t('h264_qsv (Intel)') },
                  { value: 'h264_amf', label: t('h264_amf (AMD)') },
                  { value: 'copy', label: t('copy（直拷，最省）') },
                  { value: 'libx264', label: t('libx264（CPU）') },
                  { value: 'libx265', label: t('libx265（CPU）') },
                ]}
              />
            </SettingsRow>
            <SettingsRow
              label={t('编码参数')}
              description={t('支持固定 CRF 质量、自定义目标码率或直拷不限制')}
            >
              <Select
                size="small"
                style={SELECT_W}
                value={settings.param_mode}
                onChange={(v) => handleRecordChange('param_mode', v)}
                options={[
                  { value: 'CRF 质量', label: t('CRF 质量') },
                  { value: '自定义码率', label: t('自定义码率') },
                  { value: '不限制', label: t('不限制') },
                ]}
              />
            </SettingsRow>
            <SettingsRow
              label={t('编码预设')}
              description={t('速度越快 CPU 占用越低，较慢预设画面压缩率更高')}
            >
              <Select
                size="small"
                style={SELECT_W}
                value={settings.preset || 'medium'}
                onChange={(v) => handleRecordChange('preset', v)}
                options={[
                  { value: 'ultrafast', label: t('ultrafast（最快）') },
                  { value: 'fast', label: t('fast（快速）') },
                  { value: 'medium', label: t('medium（均衡）') },
                  { value: 'slow', label: t('slow（慢速）') },
                ]}
              />
            </SettingsRow>
            {settings.param_mode === '自定义码率' && settings.encoder !== 'copy' && (
              <SettingsRow label={t('码率')}>
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
                    <span>{t('高质量')}</span>
                    <span style={{ fontWeight: 600, color: 'var(--brand-400)' }}>{t('当前 CRF：{crf}', { crf: settings.crf })}</span>
                    <span>{t('小体积')}</span>
                  </div>
                  <Slider
                    min={18}
                    max={28}
                    value={settings.crf}
                    onChange={(v) => handleRecordChange('crf', v)}
                    marks={{ 18: '18', 23: t('推荐'), 28: '28' }}
                    tooltip={{ formatter: (v) => `CRF ${v}` }}
                    style={{ width: '100%', margin: '4px 0' }}
                  />
                </div>
              </SettingsRow>
            )}
            <SettingsRow label={t('录制分辨率')}>
              <Select
                size="small"
                style={SELECT_W}
                value={settings.resolution}
                onChange={(v) => handleRecordChange('resolution', v)}
                options={[
                  { value: '原画', label: t('原画') },
                  { value: '1920:1080', label: t('1080p (1920x1080)') },
                  { value: '1280:720', label: t('720p (1280x720)') },
                  { value: '854:480', label: t('480p (854x480)') },
                ]}
              />
            </SettingsRow>
            <SettingsRow label={t('录制帧率')}>
              <Select
                size="small"
                style={SELECT_W}
                value={settings.framerate}
                onChange={(v) => handleRecordChange('framerate', v)}
                options={[
                  { value: '原画', label: t('原画') },
                  { value: '60', label: t('60 fps') },
                  { value: '30', label: t('30 fps') },
                  { value: '24', label: t('24 fps') },
                ]}
              />
            </SettingsRow>
            <SettingsRow label={t('音频编码')}>
              <Select
                size="small"
                style={SELECT_W}
                value={settings.audio_bitrate}
                onChange={(v) => handleRecordChange('audio_bitrate', v)}
                options={[
                  { value: '128k', label: t('AAC 128k') },
                  { value: '192k', label: t('AAC 192k') },
                  { value: '256k', label: t('AAC 256k') },
                ]}
              />
            </SettingsRow>
          </SettingsSection>

          <SettingsSection
            id="ai"
            title={t('AI 智能分析')}
            description={t('配置 OCR 文字识别加速与高光切片并发导出参数')}
          >
            <SettingsRow
              label={t('OCR 加速引擎')}
              description={t('用于识别游戏内比分与击杀字幕的计算加速后端')}
            >
              <Select
                size="small"
                style={SELECT_W}
                value={settings.ocr_accel || 'dml'}
                onChange={(v) => {
                  handleRecordChange('ocr_accel', v as RecordSettings['ocr_accel'])
                  message.success(t('OCR 加速已保存（下次识别生效）'), 2)
                }}
                options={[
                  { value: 'dml', label: t('DirectML（Windows GPU，推荐）') },
                  { value: 'auto', label: t('自动') },
                  { value: 'cuda', label: t('CUDA（NVIDIA）') },
                  { value: 'cpu', label: t('仅 CPU') },
                ]}
              />
            </SettingsRow>
            <SettingsRow
              label={t('共享进样')}
              description={t('预览与录制共用同一进程，节省近 50% 系统显存')}
            >
              <ToggleSwitch
                checked={!!settings.shared_ingest_enabled}
                onChange={(v) => {
                  handleRecordChange('shared_ingest_enabled', v)
                  message.success(t(v ? '已开启共享进样（新预览/录制生效）' : '已关闭共享进样（新预览/录制生效）'), 2)
                }}
              />
            </SettingsRow>
            <SettingsRow
              label={t('并发导出数')}
              description={t('限制后台同时渲染导出的切片任务数量')}
            >
              <Select
                size="small"
                style={SELECT_W}
                value={settings.export_max_concurrent ?? 2}
                onChange={(v) => {
                  handleRecordChange('export_max_concurrent', Number(v))
                  message.success(t(Number(v) === 1 ? '已设为单路导出（降低 CPU 负载）' : '已设为双路并发导出'), 2)
                }}
                options={[
                  { value: 2, label: t('2 路并发（默认推荐）') },
                  { value: 1, label: t('1 路顺序导出（低负载模式）') },
                ]}
              />
            </SettingsRow>
            <SettingsRow
              label={t('默认导出预设')}
              description={t('一键导出切片或生成剪映草稿时应用的画幅比例与参数')}
            >
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

          <SettingsSection
            id="storage"
            title={t('存储与草稿')}
            description={t('管理原始视频切片落盘路径与剪映项目工程草稿位置')}
          >
            <SettingsRow
              label={t('存储路径')}
              description={t('所有录制视频与切片片段的本地存储目录')}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', maxWidth: 360 }}>
                <span className="settings-path">{settings.output_dir}</span>
                <button type="button" onClick={() => { void handleBrowse() }} className="browse-btn">
                  <FolderOpenOutlined style={{ fontSize: 14 }} />
                  {t('浏览')}
                </button>
              </div>
            </SettingsRow>
            <SettingsRow
              label={t('剪映草稿目录')}
              description={t('自动定位剪映专业版草稿项目库，支持一键生成剪映时间线')}
            >
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, width: '100%', maxWidth: 360 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%' }}>
                  <span
                    className="settings-path"
                    style={{ color: settings.jianying_draft_dir ? 'var(--text-300)' : 'var(--text-tertiary)' }}
                  >
                    {settings.jianying_draft_dir
                      || detectedJianyingDir
                      || t('未检测到剪映，请手动选择')}
                  </span>
                  <button type="button" onClick={() => { void handleBrowseJianyingDir() }} className="browse-btn">
                    {t('更改')}
                  </button>
                  <button type="button" onClick={handleResetJianyingDir} className="browse-btn">
                    {t('恢复自动探测')}
                  </button>
                </div>
                {!settings.jianying_draft_dir && !detectedJianyingDir && (
                  <span style={{ fontSize: 11, color: 'var(--state-warning)', lineHeight: 1.5 }}>
                    {t('自动探测失败：请安装剪映专业版或手动指定草稿目录')}
                  </span>
                )}
              </div>
            </SettingsRow>
          </SettingsSection>

          <SettingsSection
            id="account"
            title={t('平台凭证 Cookie')}
            description={t('配置各直播平台的高清流解析登录凭证')}
            bodyStyle={{ padding: 0, background: 'transparent', overflow: 'visible' }}
          >
            <div style={{
              background: 'var(--bg-secondary)',
              border: '1px solid var(--border-default)',
              borderRadius: 'var(--radius-md, 8px)',
              overflow: 'hidden',
              padding: 16,
              marginBottom: 16,
            }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 8 }}>{t('抖音 Cookie')}</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, marginBottom: 10, color: douyinCookieStatus?.configured ? 'var(--state-success)' : 'var(--state-warning)' }}>
                {douyinCookieStatus?.configured
                  ? <>
                      <span>{t('已配置 {count} 项', { count: douyinCookieStatus.count || 0 })}</span>
                      {(douyinCookieStatus.keys || []).length > 0 && (
                        <Tooltip title={(douyinCookieStatus.keys || []).join(', ')}>
                          <span style={{
                            fontSize: 11,
                            color: 'var(--text-tertiary)',
                            textDecoration: 'underline dotted',
                            cursor: 'help',
                          }}>
                            {t('查看键名')}
                          </span>
                        </Tooltip>
                      )}
                    </>
                  : t('尚未配置有效 Cookie')}
              </div>
              <Input.TextArea
                value={douyinCookieText}
                onChange={(e) => setDouyinCookieText(e.target.value)}
                placeholder={t('支持 JSON 对象/数组，或 ttwid=...; sessionid=... 格式')}
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
                {t('保存抖音 Cookie')}
              </Button>
            </div>

            <div style={{
              background: 'var(--bg-secondary)',
              border: '1px solid var(--border-default)',
              borderRadius: 'var(--radius-md, 8px)',
              overflow: 'hidden',
              padding: 16,
              marginBottom: 16,
            }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 8 }}>{t('B站 Cookie')}</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, marginBottom: 10, color: bilibiliCookieStatus?.configured ? 'var(--state-success)' : 'var(--state-warning)' }}>
                {bilibiliCookieStatus?.configured
                  ? <>
                      <span>{t('已配置 {count} 项', { count: bilibiliCookieStatus.count || 0 })}</span>
                      {(bilibiliCookieStatus.keys || []).length > 0 && (
                        <Tooltip title={(bilibiliCookieStatus.keys || []).join(', ')}>
                          <span style={{
                            fontSize: 11,
                            color: 'var(--text-tertiary)',
                            textDecoration: 'underline dotted',
                            cursor: 'help',
                          }}>
                            {t('查看键名')}
                          </span>
                        </Tooltip>
                      )}
                    </>
                  : t('尚未配置有效 Cookie')}
              </div>
              <Input.TextArea
                value={bilibiliCookieText}
                onChange={(e) => setBilibiliCookieText(e.target.value)}
                placeholder={t('支持 JSON 对象/数组，或 SESSDATA=...; bili_jct=... 格式')}
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
                {t('保存 B站 Cookie')}
              </Button>
            </div>

            <div style={{
              background: 'var(--bg-secondary)',
              border: '1px solid var(--border-default)',
              borderRadius: 'var(--radius-md, 8px)',
              overflow: 'hidden',
              padding: 16,
            }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 8 }}>{t('虎牙 Cookie')}</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, marginBottom: 10, color: huyaCookieStatus?.configured ? 'var(--state-success)' : 'var(--state-warning)' }}>
                {huyaCookieStatus?.configured
                  ? <>
                      <span>{t('已配置 {count} 项', { count: huyaCookieStatus.count || 0 })}</span>
                      {(huyaCookieStatus.keys || []).length > 0 && (
                        <Tooltip title={(huyaCookieStatus.keys || []).join(', ')}>
                          <span style={{
                            fontSize: 11,
                            color: 'var(--text-tertiary)',
                            textDecoration: 'underline dotted',
                            cursor: 'help',
                          }}>
                            {t('查看键名')}
                          </span>
                        </Tooltip>
                      )}
                    </>
                  : t('尚未配置有效 Cookie')}
              </div>
              <Input.TextArea
                value={huyaCookieText}
                onChange={(e) => setHuyaCookieText(e.target.value)}
                placeholder={t('支持 JSON 对象/数组，或 udb_uid=...; udb_guid=... 格式')}
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
                {t('保存虎牙 Cookie')}
              </Button>
            </div>
          </SettingsSection>

          <SettingsSection
            id="shortcuts"
            title={t('快捷键与鼠标操作')}
            description={t('键位与 WORKBENCH_SHORTCUTS 表同源自动生成，不会与实际键位漂移')}
          >
            {SHORTCUT_DOCS.map(row => (
              <SettingsRow key={row.ids.join('+')} label={t(row.label)}>
                <KeyBadge keys={row.keys} />
              </SettingsRow>
            ))}
            {MOUSE_DOCS.map(row => (
              <SettingsRow key={row.label} label={t(row.label)} description={t(row.desc)}>
                <MouseBadge label={t('鼠标')} />
              </SettingsRow>
            ))}
          </SettingsSection>

          <SettingsSection
            id="about"
            title={t('关于与更新')}
            description={t('客户端版本信息与自动更新检查')}
          >
            <SettingsRow
              label={t('当前版本')}
              description={t('Live Stream Clipper 多人协作版')}
            >
              <span style={{ fontSize: 13, color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>v{appVersion || '1.0.0'}</span>
            </SettingsRow>
            <SettingsRow label={t('在线更新')}>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
                {updateStatus?.type === 'checking' && (
                  <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{t('正在检查更新...')}</span>
                )}
                {updateStatus?.type === 'not-available' && (
                  <span style={{ fontSize: 12, color: 'var(--state-success)' }}>
                    {t('✓ 已是最新版本 v{version}', { version: updateStatus.version ?? '' })}
                  </span>
                )}
                {updateStatus?.type === 'available' && (
                  <>
                    <span style={{ fontSize: 12, color: 'var(--brand-400)', fontWeight: 500 }}>
                      {t('发现新版本 v{version}', { version: updateStatus.version ?? '' })}
                    </span>
                    {updateStatus.releaseNotes && (
                      <span style={{
                        fontSize: 11,
                        color: 'var(--text-tertiary)',
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
                      {t('前往下载')}
                    </Button>
                  </>
                )}
                {updateStatus?.type === 'error' && (
                  <span style={{ fontSize: 12, color: 'var(--state-error)' }}>
                    {updateStatus.message || t('更新失败')}
                  </span>
                )}
                <Button onClick={() => { void handleCheckUpdate() }} loading={updateStatus?.type === 'checking'}>
                  {t('检查更新')}
                </Button>
              </div>
            </SettingsRow>
          </SettingsSection>

          <SettingsSection
            id="logs"
            title={t('运行日志')}
            description={t('查看与导出客户端与后端的实时运行日志')}
            extra={
              <Button
                type="text"
                size="small"
                icon={<FolderOutlined />}
                onClick={() => { void handleOpenLogFolder() }}
              >
                {t('打开日志目录')}
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
          {saveState === 'pending' ? t('正在保存…') : saveState === 'saved' ? t('已自动保存 · {savedAt}', { savedAt }) : t('全部已保存')}
        </span>
        <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{t('更改即时生效')}</span>
      </div>
    </div>
  )
}
