import { useEffect, useState, useRef } from 'react'
import { Button, message, Tooltip, Slider, Input } from 'antd'
import { FolderOpenOutlined, ReloadOutlined, CheckCircleFilled, CloseCircleFilled, DownloadOutlined } from '@ant-design/icons'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useAppStore } from '@/store/appStore'
import LogViewer from '@/components/LogViewer'
import { RecordSettings, AppSettings } from '@/types'
import { EXPORT_PRESETS } from '@/services/exportPresets'

export default function Settings() {
  const { isConnected, send, on } = useWebSocket()
  const settings = useAppStore((state) => state.settings)
  const setSettings = useAppStore((state) => state.setSettings)
  const appSettings = useAppStore((state) => state.appSettings)
  const setAppSettings = useAppStore((state) => state.setAppSettings)
  const dependencyStatus = useAppStore((state) => state.dependencyStatus)
  const [checkingDeps, setCheckingDeps] = useState(false)
  const depCheckTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
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

  useEffect(() => {
    // 获取应用版本号
    window.electronAPI?.getAppVersion().then((v: string) => setAppVersion(v)).catch((e: unknown) => console.error('[Settings] getAppVersion failed:', e))

    // 监听更新状态
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

  // 依赖检测响应可能通过 check_dependencies_response 事件异步到达，
  // checkingDeps 在收到响应后通过 store 变化自动消除
  useEffect(() => {
    if (dependencyStatus) setCheckingDeps(false)
  }, [dependencyStatus])

  // 启动时从主进程同步开机自启/最小化到托盘的真实状态，避免与前端 store 不一致
  useEffect(() => {
    window.app?.getAutoLaunch().then((v) => setAppSettings({ autoLaunch: v })).catch((e: unknown) => console.error('[Settings] getAutoLaunch failed:', e))
    window.app?.getMinimizeToTray().then((v) => setAppSettings({ minimizeToTray: v })).catch((e: unknown) => console.error('[Settings] getMinimizeToTray failed:', e))
  }, [setAppSettings])

  const handleRecordChange = <K extends keyof RecordSettings>(key: K, value: RecordSettings[K]) => {
    console.log('[Settings] 用户修改录制参数:', key, '->', value);

    setSettings({ [key]: value })
  }

  // 主题切换：更新 store + 实时切换 documentElement class（持久化由 handleSave 统一处理）
  const handleThemeChange = (value: AppSettings['theme']) => {
    console.log('[Settings] 用户切换主题:', value);

    setAppSettings({ theme: value })
    if (value === 'dark') {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
    send('save_settings', { ...settings, appSettings: { ...appSettings, theme: value } })
  }

  // 开机自启：调用主进程 IPC + 同步 store
  const handleAutoLaunchChange = (v: boolean) => {
    console.log('[Settings] 用户修改开机自启:', v);

    window.app?.setAutoLaunch(v)
    setAppSettings({ autoLaunch: v })
  }

  // 最小化到托盘：调用主进程 IPC + 同步 store
  const handleMinimizeToTrayChange = (v: boolean) => {
    console.log('[Settings] 用户修改最小化到托盘:', v);

    window.app?.setMinimizeToTray(v)
    setAppSettings({ minimizeToTray: v })
  }

  const handleSave = () => {
    console.log('[Settings] 用户保存设置');

    send('save_settings', { ...settings, appSettings })
    message.success('设置已保存')
  }

  const handleBrowse = async () => {
    console.log('[Settings] 用户点击选择存储路径浏览按钮');

    if (window.electronAPI) {
      const dir = await window.electronAPI.selectDirectory()
      if (dir) {
        handleRecordChange('output_dir', dir)
      }
    } else {
      message.info('请在 Electron 桌面版中使用目录选择功能')
    }
  }

  const handleCheckUpdate = async () => {
    console.log('[Settings] 用户点击检查更新');

    setUpdateStatus(null)
    const result = await window.electronAPI?.checkForUpdate()
    if (result && !result.success) {
      message.error(`检查更新失败: ${result.error}`)
    }
  }

  const handleDownloadUpdate = async () => {
    console.log('[Settings] 用户点击前往下载更新');

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

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ flex: 1, overflowY: 'auto', padding: 16 }}>

      <div style={{
        display: 'grid',
        gridTemplateColumns: '1fr',
        gap: 24,
        width: '100%',
      }}>
        {/* 通用设置 */}
        <div style={{ marginBottom: 20 }}>
          <div style={{
            fontSize: 13,
            fontWeight: 600,
            color: 'var(--text-300)',
            textTransform: 'uppercase',
            letterSpacing: '0.04em',
            padding: '0 4px',
            marginBottom: 8,
          }}>通用设置</div>
          <div style={{
            background: 'var(--background-800)',
            borderRadius: 14,
            overflow: 'hidden',
          }}>
            <SettingsRow label="主题">
              <select
                value={appSettings.theme}
                onChange={e => handleThemeChange(e.target.value as AppSettings['theme'])}
                className="settings-select"
              >
                <option value="dark">深色</option>
                <option value="light">浅色</option>
              </select>
            </SettingsRow>
            <SettingsRow label="开机自启">
              <ToggleSwitch
                checked={appSettings.autoLaunch}
                onChange={handleAutoLaunchChange}
              />
            </SettingsRow>
            <SettingsRow label="最小化到托盘">
              <ToggleSwitch
                checked={appSettings.minimizeToTray}
                onChange={handleMinimizeToTrayChange}
              />
            </SettingsRow>
          </div>
        </div>

        {/* 系统环境 */}
        <div style={{ marginBottom: 20 }}>
          <div style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            fontSize: 13,
            fontWeight: 600,
            color: 'var(--text-300)',
            textTransform: 'uppercase',
            letterSpacing: '0.04em',
            padding: '0 4px',
            marginBottom: 8,
          }}>
            <span>系统环境</span>
            <Button
              type="text"
              size="small"
              icon={<ReloadOutlined spin={checkingDeps} />}
              onClick={handleRecheckDeps}
              disabled={!isConnected || checkingDeps}
            >
              重新检测
            </Button>
          </div>
          <div style={{
            background: 'var(--background-800)',
            borderRadius: 14,
            overflow: 'hidden',
          }}>
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
          </div>
        </div>

        {/* 录制设置 */}
        <div style={{ marginBottom: 20 }}>
          <div style={{
            fontSize: 13,
            fontWeight: 600,
            color: 'var(--text-300)',
            textTransform: 'uppercase',
            letterSpacing: '0.04em',
            padding: '0 4px',
            marginBottom: 8,
          }}>录制设置</div>
          <div style={{
            background: 'var(--background-800)',
            borderRadius: 14,
            overflow: 'hidden',
          }}>
            <SettingsRow label="默认画质">
              <select
                value={settings.quality}
                onChange={e => handleRecordChange('quality', e.target.value)}
                className="settings-select"
              >
                <option value="原画">原画</option>
                <option value="蓝光">蓝光</option>
                <option value="超清">超清</option>
                <option value="高清">高清</option>
                <option value="流畅">流畅</option>
              </select>
            </SettingsRow>
            <SettingsRow label="预览画质">
              <select
                value={settings.preview_quality}
                onChange={e => handleRecordChange('preview_quality', e.target.value)}
                className="settings-select"
              >
                <option value="原画">原画（不缩放）</option>
                <option value="高清">高清 720p</option>
                <option value="标清">标清 480p</option>
                <option value="流畅">流畅 360p</option>
              </select>
            </SettingsRow>
             <SettingsRow label="共享进样">
               <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'flex-end', maxWidth: 360 }}>
                 <ToggleSwitch
                   checked={!!settings.shared_ingest_enabled}
                   onChange={(v) => {
                     handleRecordChange('shared_ingest_enabled', v)
                     send('save_settings', { ...settings, shared_ingest_enabled: v, appSettings })
                     message.success(v ? '已开启共享进样（新预览/录制生效）' : '已关闭共享进样（新预览/录制生效）', 2)
                   }}
                 />
                <div style={{ fontSize: 11, color: 'var(--state-warning)', lineHeight: 1.5, textAlign: 'right' }}>
                  开启后预览与录制共用同一进程：录制中断会导致预览中断，预览转码可能影响录制稳定性。
                </div>
              </div>
            </SettingsRow>
            <SettingsRow label="OCR 加速">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'flex-end', maxWidth: 360 }}>
                <select
                  value={settings.ocr_accel || 'dml'}
                  onChange={(e) => {
                    const v = e.target.value as 'auto' | 'dml' | 'cuda' | 'cpu'
                    handleRecordChange('ocr_accel', v)
                    send('save_settings', { ...settings, ocr_accel: v, appSettings })
                    message.success('OCR 加速已保存（下次识别生效）', 2)
                  }}
                  className="settings-select"
                >
                  <option value="dml">DirectML（Windows GPU，推荐）</option>
                  <option value="auto">自动</option>
                  <option value="cuda">CUDA（NVIDIA）</option>
                  <option value="cpu">仅 CPU</option>
                </select>
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)', lineHeight: 1.5, textAlign: 'right' }}>
                  持续分析 OCR 推理加速；自动会探测并选最快后端，弱核显可能回退 CPU。
                </div>
              </div>
            </SettingsRow>
            <SettingsRow label="默认编码器">
              <select
                value={settings.encoder}
                onChange={e => handleRecordChange('encoder', e.target.value)}
                className="settings-select"
              >
                <option value="h264_nvenc">h264_nvenc (NVIDIA，推荐)</option>
                <option value="hevc_nvenc">hevc_nvenc (NVIDIA)</option>
                <option value="h264_qsv">h264_qsv (Intel)</option>
                <option value="h264_amf">h264_amf (AMD)</option>
                <option value="copy">copy（直拷，最省）</option>
                <option value="libx264">libx264（CPU）</option>
                <option value="libx265">libx265（CPU）</option>
              </select>
            </SettingsRow>
            <SettingsRow label="编码参数">
              <select
                value={settings.param_mode}
                onChange={e => handleRecordChange('param_mode', e.target.value)}
                className="settings-select"
              >
                <option value="CRF 质量">CRF 质量</option>
                 <option value="自定义码率">自定义码率</option>
                <option value="不限制">不限制</option>
              </select>
            </SettingsRow>
            <SettingsRow label="编码预设">
              <select
                value={settings.preset || 'medium'}
                onChange={e => handleRecordChange('preset', e.target.value)}
                className="settings-select"
              >
                <option value="ultrafast">ultrafast（最快）</option>
                <option value="fast">fast（快速）</option>
                <option value="medium">medium（均衡）</option>
                <option value="slow">slow（慢速）</option>
              </select>
            </SettingsRow>
             {settings.param_mode === '自定义码率' && settings.encoder !== 'copy' && (
              <SettingsRow label="码率">
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <select
                    value={settings.bitrate_unit}
                    onChange={e => handleRecordChange('bitrate_unit', e.target.value)}
                    className="settings-select"
                    style={{ width: 80 }}
                  >
                    <option value="kbps">kbps</option>
                    <option value="Mbps">Mbps</option>
                  </select>
                  <select
                    value={String(settings.bitrate)}
                    onChange={e => handleRecordChange('bitrate', e.target.value)}
                    className="settings-select"
                    style={{ flex: 1 }}
                  >
                    {[1000, 2000, 4000, 6000, 8000, 10000, 12000, 15000, 20000].map(b => (
                      <option key={b} value={String(b)}>{b}</option>
                    ))}
                  </select>
                </div>
              </SettingsRow>
            )}
            {settings.param_mode === 'CRF 质量' && settings.encoder !== 'copy' && (
              <SettingsRow label="CRF">
                <div style={{ width: '100%', padding: '0 4px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text-tertiary)' }}>
                    <span>18（高质量，大体积）</span>
                    <span style={{ fontWeight: 600, color: 'var(--brand-400)' }}>CRF {settings.crf}</span>
                    <span>28（低质量，小体积）</span>
                  </div>
                  <Slider
                    min={18}
                    max={28}
                    value={settings.crf}
                    onChange={(v) => handleRecordChange('crf', v)}
                    marks={{ 18: '', 23: '23', 28: '' }}
                    tooltip={{ open: false }}
                    style={{ width: '100%', margin: '4px 0' }}
                  />
                </div>
              </SettingsRow>
            )}
            <SettingsRow label="录制分辨率">
              <select
                value={settings.resolution}
                onChange={e => handleRecordChange('resolution', e.target.value)}
                className="settings-select"
              >
                <option value="原画">原画</option>
                <option value="1920:1080">1080p (1920x1080)</option>
                <option value="1280:720">720p (1280x720)</option>
                <option value="854:480">480p (854x480)</option>
              </select>
            </SettingsRow>
            <SettingsRow label="录制帧率">
              <select
                value={settings.framerate}
                onChange={e => handleRecordChange('framerate', e.target.value)}
                className="settings-select"
              >
                <option value="原画">原画</option>
                <option value="60">60 fps</option>
                <option value="30">30 fps</option>
                <option value="24">24 fps</option>
              </select>
            </SettingsRow>
            <SettingsRow label="音频编码">
              <select
                value={settings.audio_bitrate}
                onChange={e => handleRecordChange('audio_bitrate', e.target.value)}
                className="settings-select"
              >
                <option value="128k">AAC 128k</option>
                <option value="192k">AAC 192k</option>
                <option value="256k">AAC 256k</option>
              </select>
            </SettingsRow>
            <SettingsRow label="存储路径">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{
                  background: 'var(--background-700)',
                  color: 'var(--text-300)',
                  border: '1px solid var(--border-default)',
                  borderRadius: 10,
                  padding: '6px 12px',
                  fontSize: 12,
                  fontFamily: 'var(--font-mono)',
                  flex: 1,
                  minWidth: 0,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}>
                  {settings.output_dir}
                </span>
                <button onClick={handleBrowse} className="browse-btn">
                  <FolderOpenOutlined style={{ fontSize: 14 }} />
                  浏览
                </button>
              </div>
            </SettingsRow>
             <SettingsRow label="并发导出数">
               <select
                 value={settings.export_max_concurrent ?? 2}
                 onChange={e => {
                   const v = Number(e.target.value)
                   handleRecordChange('export_max_concurrent', v)
                   send('save_settings', { ...settings, export_max_concurrent: v, appSettings })
                   message.success(v === 1 ? '已设为单路导出（降低 CPU 负载）' : '已设为双路并发导出', 2)
                 }}
                 className="settings-select"
               >
                 <option value="2">2 路（默认）</option>
                 <option value="1">1 路（低负载）</option>
               </select>
             </SettingsRow>
             <SettingsRow label="默认导出预设">
              <select
                value={appSettings.default_export_preset || 'douyin_vertical'}
                onChange={e => {
                  const newPresetId = e.target.value
                  setAppSettings({ default_export_preset: newPresetId })
                  send('save_settings', { ...settings, appSettings: { ...appSettings, default_export_preset: newPresetId } })
                }}
                className="settings-select"
              >
                {EXPORT_PRESETS.map(p => (
                  <option key={p.id} value={p.id}>{p.name} — {p.description}</option>
                ))}
              </select>
            </SettingsRow>
          </div>
        </div>

        {/* 快捷键 */}
        <div style={{ marginBottom: 20 }}>
          <div style={{
            fontSize: 13,
            fontWeight: 600,
            color: 'var(--text-300)',
            textTransform: 'uppercase',
            letterSpacing: '0.04em',
            padding: '0 4px',
            marginBottom: 8,
          }}>快捷键</div>
          <div style={{
            background: 'var(--background-800)',
            borderRadius: 14,
            overflow: 'hidden',
          }}>
            <SettingsRow label="页面：工作台">
              <KeyBadge keys={['Ctrl', '1']} />
            </SettingsRow>
            <SettingsRow label="页面：设置">
              <KeyBadge keys={['Ctrl', '2']} />
            </SettingsRow>
            <SettingsRow label="刷新页面">
              <KeyBadge keys={['F5']} />
            </SettingsRow>
            <SettingsRow label="播放/暂停">
              <KeyBadge keys={['Space']} />
            </SettingsRow>
            <SettingsRow label="标记入点">
              <KeyBadge keys={['I']} />
            </SettingsRow>
            <SettingsRow label="标记出点">
              <KeyBadge keys={['O']} />
            </SettingsRow>
            <SettingsRow label="切换录制">
              <KeyBadge keys={['R']} />
            </SettingsRow>
            <SettingsRow label="静音/取消静音">
              <KeyBadge keys={['M']} />
            </SettingsRow>
            <SettingsRow label="全屏预览">
              <KeyBadge keys={['F']} />
            </SettingsRow>
            <SettingsRow label="批量开始录制">
              <KeyBadge keys={['Ctrl', 'R']} />
            </SettingsRow>
            <SettingsRow label="批量停止录制">
              <KeyBadge keys={['Ctrl', 'Shift', 'R']} />
            </SettingsRow>
            <SettingsRow label="全选房间">
              <KeyBadge keys={['Ctrl', 'Shift', 'A']} />
            </SettingsRow>
            <SettingsRow label="导出切片">
              <KeyBadge keys={['Ctrl', 'E']} />
            </SettingsRow>
          </div>
        </div>

        {/* 抖音 Cookie */}
        <div style={{ marginBottom: 20 }}>
          <div style={{
            fontSize: 13,
            fontWeight: 600,
            color: 'var(--text-300)',
            textTransform: 'uppercase',
            letterSpacing: '0.04em',
            padding: '0 4px',
            marginBottom: 8,
          }}>抖音 Cookie</div>
          <div style={{
            background: 'var(--background-800)',
            borderRadius: 14,
            overflow: 'hidden',
            padding: 16,
          }}>
            <div style={{ fontSize: 12, color: 'var(--text-400)', lineHeight: 1.6, marginBottom: 10 }}>
              Chrome 新版无法自动读取 Cookie。请在浏览器登录抖音后，用 Cookie-Editor 等插件导出 JSON，粘贴到下方并保存，否则直播间会一直连不上（显示验证页/未开播）。
            </div>
            <div style={{ fontSize: 12, marginBottom: 10, color: douyinCookieStatus?.configured ? 'var(--state-success)' : 'var(--state-warning)' }}>
              {douyinCookieStatus?.configured
                ? `已配置 ${douyinCookieStatus.count || 0} 项（${(douyinCookieStatus.keys || []).slice(0, 6).join(', ') || '已保存'}）`
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
        </div>

        {/* 关于 */}
        <div style={{ marginBottom: 20 }}>
          <div style={{
            fontSize: 13,
            fontWeight: 600,
            color: 'var(--text-300)',
            textTransform: 'uppercase',
            letterSpacing: '0.04em',
            padding: '0 4px',
            marginBottom: 8,
          }}>关于</div>
          <div style={{
            background: 'var(--background-800)',
            borderRadius: 14,
            overflow: 'hidden',
          }}>
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
                      🎉 发现新版本 v{updateStatus.version}
                    </span>
                    {updateStatus.message && (
                      <span style={{
                        fontSize: 11,
                        color: 'var(--text-400)',
                        maxWidth: 220,
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                        lineHeight: 1.5,
                      }}>
                        {String(updateStatus.message).slice(0, 120)}{String(updateStatus.message).length > 120 ? '...' : ''}
                      </span>
                    )}
                    <Button
                      type="primary"
                      size="small"
                      icon={<DownloadOutlined />}
                      onClick={handleDownloadUpdate}
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
                <Button
                  onClick={handleCheckUpdate}
                  loading={updateStatus?.type === 'checking'}
                >
                  检查更新
                </Button>
              </div>
            </SettingsRow>
          </div>
        </div>

        {/* 日志 */}
        <div style={{ marginBottom: 20 }}>
          <div style={{
            fontSize: 13,
            fontWeight: 600,
            color: 'var(--text-300)',
            textTransform: 'uppercase',
            letterSpacing: '0.04em',
            padding: '0 4px',
            marginBottom: 8,
          }}>日志</div>
          <div style={{
            background: 'var(--background-800)',
            borderRadius: 14,
            padding: 16,
          }}>
            <LogViewer />
          </div>
        </div>
      </div>

      <style>{`
        .settings-select {
          appearance: none;
          -webkit-appearance: none;
          background: var(--background-700);
          color: var(--text-50);
          border: 1px solid var(--border-default);
          border-radius: 10px;
          padding: 6px 32px 6px 12px;
          font-size: 13px;
          font-family: inherit;
          cursor: pointer;
          outline: none;
          min-width: 140px;
          background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%238e8e93' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
          background-repeat: no-repeat;
          background-position: right 10px center;
          transition: border-color 0.15s ease;
        }
        .settings-select:hover {
          border-color: var(--brand-400);
        }
        .settings-select:focus {
          border-color: var(--brand-500);
        }
        .settings-number {
          background: var(--background-700);
          color: var(--text-50);
          border: 1px solid var(--border-default);
          border-radius: 10px;
          padding: 6px 12px;
          font-size: 13px;
          font-family: inherit;
          width: 60px;
          text-align: center;
          outline: none;
          transition: border-color 0.15s ease;
        }
        .settings-number:hover {
          border-color: var(--brand-400);
        }
        .settings-number:focus {
          border-color: var(--brand-500);
        }
        .browse-btn {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          background: var(--bg-tertiary);
          color: var(--brand-400);
          border: 1px solid var(--border-default);
          border-radius: 10px;
          padding: 6px 14px;
          font-size: 12px;
          font-family: inherit;
          font-weight: 500;
          cursor: pointer;
          transition: background 0.15s ease, border-color 0.15s ease;
          white-space: nowrap;
        }
        .browse-btn:hover {
          background: var(--brand-500);
          color: #fff;
          border-color: var(--brand-500);
        }
      `}</style>
      </div>
      {/* 固定底栏 — 保存按钮始终可见 */}
      <div style={{
        padding: '12px 16px',
        borderTop: '1px solid var(--border-default)',
        background: 'var(--background-800)',
        display: 'flex',
        justifyContent: 'flex-end',
        flexShrink: 0,
      }}>
        <Button type="primary" onClick={handleSave}>保存设置</Button>
      </div>
    </div>
  )
}

function DepStatus({ ok, version, path: depPath }: { ok: boolean | undefined; version?: string; path?: string }) {
  if (ok === undefined) {
    return <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>检测中...</span>
  }
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      {ok ? (
        <CheckCircleFilled style={{ color: '#52c41a', fontSize: 14 }} />
      ) : (
        <CloseCircleFilled style={{ color: '#ff4d4f', fontSize: 14 }} />
      )}
      {version && (
        <Tooltip title={depPath || ''}>
          <span style={{
            fontSize: 12,
            color: ok ? 'var(--text-secondary)' : 'var(--state-error)',
            maxWidth: 200,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}>
            {version}
          </span>
        </Tooltip>
      )}
    </div>
  )
}

function SettingsRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '12px 16px',
      borderBottom: '1px solid var(--border-default)',
      minHeight: 44,
      flexWrap: 'wrap',
      gap: 8,
    }}>
      <span style={{ fontSize: 13, fontWeight: 400, color: 'var(--text-50)', whiteSpace: 'nowrap' }}>
        {label}
      </span>
      {children}
    </div>
  )
}

function ToggleSwitch({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label style={{
      position: 'relative',
      display: 'inline-block',
      width: 44,
      height: 26,
      flexShrink: 0,
      cursor: 'pointer',
    }}>
      <input
        type="checkbox"
        checked={checked}
        onChange={e => onChange(e.target.checked)}
        style={{ opacity: 0, width: 0, height: 0, position: 'absolute' }}
      />
      <span style={{
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        background: checked ? 'var(--state-success)' : 'var(--background-700)',
        borderRadius: 13,
        transition: 'background 0.25s ease',
      }}>
        <span style={{
          position: 'absolute',
          height: 22,
          width: 22,
          left: 2,
          bottom: 2,
          background: 'var(--background-50)',
          borderRadius: '50%',
          transition: 'transform 0.25s ease',
          transform: checked ? 'translateX(18px)' : 'translateX(0)',
          boxShadow: '0 1px 3px rgba(0, 0, 0, 0.3)',
        }} />
      </span>
    </label>
  )
}

function KeyBadge({ keys }: { keys: string[] }) {
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 4,
      background: 'var(--background-700)',
      border: '1px solid var(--border-default)',
      borderRadius: 6,
      padding: '3px 10px',
      fontFamily: 'var(--font-mono)',
      fontSize: 12,
      color: 'var(--text-50)',
      whiteSpace: 'nowrap',
    }}>
      {keys.map((key, i) => (
        <span key={i}>
          {i > 0 && ' + '}
          <kbd style={{ fontFamily: 'inherit', fontSize: 12, color: 'var(--text-50)' }}>{key}</kbd>
        </span>
      ))}
    </span>
  )
}
