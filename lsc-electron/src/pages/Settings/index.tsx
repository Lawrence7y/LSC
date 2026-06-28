import { useEffect, useState } from 'react'
import { Button, message, Tooltip, Progress } from 'antd'
import { FolderOpenOutlined, ReloadOutlined, CheckCircleFilled, CloseCircleFilled, DownloadOutlined } from '@ant-design/icons'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useAppStore } from '@/store/appStore'
import { RecordSettings, AppSettings } from '@/types'

export default function Settings() {
  const { isConnected, send } = useWebSocket()
  const settings = useAppStore((state) => state.settings)
  const setSettings = useAppStore((state) => state.setSettings)
  const appSettings = useAppStore((state) => state.appSettings)
  const setAppSettings = useAppStore((state) => state.setAppSettings)
  const dependencyStatus = useAppStore((state) => state.dependencyStatus)
  const [checkingDeps, setCheckingDeps] = useState(false)
  const [appVersion, setAppVersion] = useState('')
  const [updateStatus, setUpdateStatus] = useState<{
    type: string
    version?: string
    percent?: number
    message?: string
  } | null>(null)

  useEffect(() => {
    // 获取应用版本号
    window.electronAPI?.getAppVersion().then((v: string) => setAppVersion(v))

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
    }
  }, [isConnected, send])

  const handleRecheckDeps = () => {
    if (!isConnected) return
    setCheckingDeps(true)
    send('check_dependencies', {})
    setTimeout(() => setCheckingDeps(false), 5000)
  }

  // 依赖检测响应可能通过 check_dependencies_response 事件异步到达，
  // checkingDeps 在收到响应后通过 store 变化自动消除
  useEffect(() => {
    if (dependencyStatus) setCheckingDeps(false)
  }, [dependencyStatus])

  // 启动时从主进程同步开机自启/最小化到托盘的真实状态，避免与前端 store 不一致
  useEffect(() => {
    window.app?.getAutoLaunch().then((v) => setAppSettings({ autoLaunch: v }))
    window.app?.getMinimizeToTray().then((v) => setAppSettings({ minimizeToTray: v }))
  }, [setAppSettings])

  const handleRecordChange = <K extends keyof RecordSettings>(key: K, value: RecordSettings[K]) => {
    setSettings({ [key]: value })
  }

  // 主题切换：更新 store + 实时切换 documentElement class（持久化由 handleSave 统一处理）
  const handleThemeChange = (value: AppSettings['theme']) => {
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
    window.app?.setAutoLaunch(v)
    setAppSettings({ autoLaunch: v })
  }

  // 最小化到托盘：调用主进程 IPC + 同步 store
  const handleMinimizeToTrayChange = (v: boolean) => {
    window.app?.setMinimizeToTray(v)
    setAppSettings({ minimizeToTray: v })
  }

  const handleSave = () => {
    send('save_settings', { ...settings, appSettings })
    message.success('设置已保存')
  }

  const handleBrowse = async () => {
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
    setUpdateStatus(null)
    const result = await window.electronAPI?.checkForUpdate()
    if (result && !result.success) {
      message.error(`检查更新失败: ${result.error}`)
    }
  }

  const handleDownloadUpdate = async () => {
    const result = await window.electronAPI?.downloadUpdate()
    if (result && !result.success) {
      message.error(`下载失败: ${result.error}`)
    }
  }

  const handleInstallUpdate = () => {
    window.electronAPI?.installUpdate()
  }

  return (
    <div style={{ padding: 24 }}>
      <div style={{ 
        display: 'flex', 
        justifyContent: 'space-between', 
        alignItems: 'center',
        marginBottom: 24,
      }}>
        <h1 style={{ fontSize: 17, fontWeight: 600, color: 'var(--text-50)', margin: 0 }}>
          设置
        </h1>
        <Button type="primary" onClick={handleSave}>保存设置</Button>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))',
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
            <SettingsRow label="语言">
              <select
                value={appSettings.language}
                onChange={e => {
                  const newLang = e.target.value as AppSettings['language']
                  setAppSettings({ language: newLang })
                  send('save_settings', { ...settings, appSettings: { ...appSettings, language: newLang } })
                }}
                className="settings-select"
              >
                <option value="zh-CN">简体中文</option>
                <option value="zh-TW">繁體中文</option>
                <option value="en">English</option>
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
            <SettingsRow label="默认编码器">
              <select
                value={settings.encoder}
                onChange={e => handleRecordChange('encoder', e.target.value)}
                className="settings-select"
              >
                <option value="libx264">libx264</option>
                <option value="libx265">libx265</option>
                <option value="copy">copy</option>
                <option value="h264_nvenc">h264_nvenc</option>
                <option value="hevc_nvenc">hevc_nvenc</option>
              </select>
            </SettingsRow>
            <SettingsRow label="CRF">
              <input 
                type="number" 
                value={settings.crf}
                onChange={e => handleRecordChange('crf', parseInt(e.target.value))}
                min={0}
                max={51}
                className="settings-number"
              />
            </SettingsRow>
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
                  maxWidth: 200,
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
            <SettingsRow label="开始/停止录制">
              <KeyBadge keys={['Ctrl', 'Shift', 'R']} />
            </SettingsRow>
            <SettingsRow label="手动切片">
              <KeyBadge keys={['Ctrl', 'Shift', 'C']} />
            </SettingsRow>
            <SettingsRow label="截图">
              <KeyBadge keys={['Ctrl', 'Shift', 'S']} />
            </SettingsRow>
            <SettingsRow label="设置入点">
              <KeyBadge keys={['I']} />
            </SettingsRow>
            <SettingsRow label="设置出点">
              <KeyBadge keys={['O']} />
            </SettingsRow>
            <SettingsRow label="播放/暂停">
              <KeyBadge keys={['Space']} />
            </SettingsRow>
          </div>
        </div>

        {/* 关于 */}
        <div>
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
                    <span style={{ fontSize: 12, color: 'var(--brand-400)' }}>
                      发现新版本 v{updateStatus.version}
                    </span>
                    <Button type="primary" size="small" icon={<DownloadOutlined />} onClick={handleDownloadUpdate}>
                      下载更新
                    </Button>
                  </>
                )}
                {updateStatus?.type === 'downloading' && (
                  <div style={{ width: 200 }}>
                    <div style={{ fontSize: 12, color: 'var(--text-400)', marginBottom: 4 }}>
                      下载中 {updateStatus.percent}%
                    </div>
                    <Progress percent={updateStatus.percent} showInfo={false} size="small" />
                  </div>
                )}
                {updateStatus?.type === 'downloaded' && (
                  <>
                    <span style={{ fontSize: 12, color: 'var(--state-success)' }}>
                      v{updateStatus.version} 已下载完成
                    </span>
                    <Button type="primary" size="small" onClick={handleInstallUpdate}>
                      立即安装并重启
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
                  disabled={updateStatus?.type === 'downloading'}
                >
                  检查更新
                </Button>
              </div>
            </SettingsRow>
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
