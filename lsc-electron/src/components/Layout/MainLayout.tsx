import { useEffect, useState, useCallback, useRef } from 'react'
import { Outlet, useNavigate } from 'react-router-dom'
import { Layout, Button, Modal, Tooltip } from 'antd'
import {
  SettingOutlined,
  BulbOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useAppStore } from '@/store/appStore'
import { useKeyboardShortcuts, WORKBENCH_SHORTCUTS } from '@/hooks/useKeyboardShortcuts'
import { useI18n } from '@/i18n'
import type { ConnectionStatus } from '@/store/appStore'
import SystemMonitor from './SystemMonitor'
import { PillNotification } from './PillNotification'
import Settings from '@/pages/Settings'

const { Content } = Layout

const connectionDotColors: Record<ConnectionStatus, string> = {
  connected: 'var(--state-success)',
  connecting: 'var(--state-warning)',
  disconnected: 'var(--state-error)',
  reconnect_failed: 'var(--state-error)',
}

export default function MainLayout() {
  const navigate = useNavigate()
  const { t } = useI18n()
  const { send, reconnect, restartBackend } = useWebSocket()
  const connectionStatus = useAppStore((state) => state.connectionStatus)
  const backendUnresponsive = useAppStore((state) => state.backendUnresponsive)
  const appSettings = useAppStore((state) => state.appSettings)
  const setAppSettings = useAppStore((state) => state.setAppSettings)
  const settingsDrawerOpen = useAppStore((state) => state.settingsDrawerOpen)
  const setSettingsDrawerOpen = useAppStore((state) => state.setSettingsDrawerOpen)
  const [connectionVisible, setConnectionVisible] = useState(false)
  const themeTransitionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const connectionLabels: Record<ConnectionStatus, string> = {
    connected: t('已连接'),
    connecting: t('连接中'),
    disconnected: t('未连接'),
    reconnect_failed: t('连接失败'),
  }

  // 卸载时清理主题过渡定时器
  useEffect(() => () => {
    if (themeTransitionTimerRef.current) clearTimeout(themeTransitionTimerRef.current)
  }, [])

  // 连接断开时延迟 2 秒再显示 banner，避免 WS 短暂重连期间误报「无法连接到后端」。
  useEffect(() => {
    if (connectionStatus === 'disconnected') {
      const timer = setTimeout(() => setConnectionVisible(true), 2000)
      return () => clearTimeout(timer)
    }
    setConnectionVisible(false)
  }, [connectionStatus])

  // 启动时应用持久化的主题；后续主题变化时同步 documentElement class
  useEffect(() => {
    if (appSettings.theme === 'dark') {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
  }, [appSettings.theme])

  // 主题切换：更新 store + 实时切换 class + 持久化到后端（青黑 / 青白）
  const handleToggleTheme = () => {
    const { settings, appSettings: currentAppSettings } = useAppStore.getState()
    const newTheme = currentAppSettings.theme === 'dark' ? 'light' : 'dark'
    document.documentElement.classList.add('theme-transition')
    if (themeTransitionTimerRef.current) clearTimeout(themeTransitionTimerRef.current)
    themeTransitionTimerRef.current = setTimeout(() => {
      themeTransitionTimerRef.current = null
      document.documentElement.classList.remove('theme-transition')
    }, 400)
    if (newTheme === 'dark') {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
    setAppSettings({ theme: newTheme })
    send('save_settings', {
      ...settings,
      appSettings: { ...currentAppSettings, theme: newTheme },
    })
  }

  // 全局页面导航快捷键（键位与 Workbench 共用同一张表）
  useKeyboardShortcuts(
    [
      WORKBENCH_SHORTCUTS.PAGE_WORKBENCH,
      WORKBENCH_SHORTCUTS.PAGE_SETTINGS,
      WORKBENCH_SHORTCUTS.RELOAD_PAGE,
    ],
    useCallback(
      (id: string) => {
        if (id === 'page:workbench') navigate('/workbench')
        else if (id === 'page:settings') setSettingsDrawerOpen(true)
        else if (id === 'page:reload') window.location.reload()
      },
      [navigate, setSettingsDrawerOpen]
    )
  )

  return (
    <Layout style={{ height: '100vh', overflow: 'hidden', display: 'flex', flexDirection: 'column', background: 'var(--bg-primary)' }}>
      {/* 现代化顶栏 Command Header (42px 极窄精密质感) */}
      <header
        style={{
          height: 42,
          background: 'var(--surface-1)',
          borderBottom: '1px solid var(--border-hairline)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 16px',
          flexShrink: 0,
          zIndex: 50,
          userSelect: 'none',
        }}
      >
        {/* 左侧：Logo 与产品名 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
          <img
            src="./assets/logo.png"
            alt="LSC Logo"
            style={{
              width: 24,
              height: 24,
              borderRadius: 'var(--radius-xs)',
              objectFit: 'cover',
              flexShrink: 0,
            }}
          />
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
            <span
              style={{
                fontSize: 14,
                fontWeight: 700,
                letterSpacing: '0.04em',
                color: 'var(--text-primary)',
                lineHeight: 1,
              }}
            >
              LSC
            </span>
            <span
              style={{
                fontSize: 11,
                color: 'var(--text-tertiary)',
                fontWeight: 400,
                letterSpacing: '0.02em',
              }}
            >
              Live Stream Clipper
            </span>
          </div>
        </div>

        {/* 中间：集群连接状态与系统负载胶囊（下方挂载灵动通知） */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ position: 'relative', display: 'inline-flex' }}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '2px 10px',
                borderRadius: 'var(--radius-xs)',
                background: 'var(--surface-2)',
                border: '1px solid var(--border-hairline)',
                fontSize: 11,
                height: 26,
                whiteSpace: 'nowrap',
              }}
            >
              <div
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: '50%',
                  background: connectionDotColors[connectionStatus],
                  boxShadow:
                    connectionStatus === 'connecting'
                      ? `0 0 0 2px rgba(255, 149, 0, 0.2), 0 0 8px ${connectionDotColors[connectionStatus]}`
                      : `0 0 8px ${connectionDotColors[connectionStatus]}`,
                  flexShrink: 0,
                }}
              />
              <span style={{ fontWeight: 600, color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                {connectionLabels[connectionStatus]}
              </span>
              <span style={{ color: 'var(--border-hairline)', margin: '0 2px' }}>|</span>
              <SystemMonitor />
            </div>
            {/* 灵动通知层：从状态框下边缘向下冒出 */}
            <PillNotification />
          </div>

          {/* 重连按钮：仅在断开/后端假死时显示（backendUnresponsive = 心跳超时，WS 仍连着） */}
          {(connectionStatus === 'disconnected' || connectionStatus === 'reconnect_failed' || backendUnresponsive) && (
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={connectionStatus === 'reconnect_failed' || backendUnresponsive ? restartBackend : reconnect}
              style={{ fontSize: 11, height: 26, borderRadius: 'var(--radius-xs)' }}
            >
              {t('重新连接')}
            </Button>
          )}
        </div>

        {/* 右侧：双主题切换与设置 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Tooltip title={appSettings.theme === 'dark' ? t('切换为浅色模式（青+白）') : t('切换为暗色模式（青+黑）')}>
            <Button
              type="text"
              size="small"
              icon={<BulbOutlined style={{ color: 'var(--brand-500)', fontSize: 13 }} />}
              onClick={handleToggleTheme}
              style={{
                color: 'var(--text-secondary)',
                fontSize: 11,
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                height: 26,
                padding: '0 8px',
                borderRadius: 'var(--radius-xs)',
              }}
            >
              {appSettings.theme === 'dark' ? t('青黑') : t('青白')}
            </Button>
          </Tooltip>

          <Button
            type="text"
            size="small"
            icon={<SettingOutlined style={{ fontSize: 13 }} />}
            onClick={() => setSettingsDrawerOpen(true)}
            style={{
              color: 'var(--text-secondary)',
              fontSize: 11,
              height: 26,
              padding: '0 8px',
              borderRadius: 'var(--radius-xs)',
            }}
          >
            {t('设置')}
          </Button>
        </div>
      </header>

      {/* Connection Status Banner */}
      {connectionStatus !== 'connected' && connectionVisible && (
        <div
          style={{
            height: 32,
            background: 'var(--state-error)',
            color: '#ffffff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 8,
            fontSize: 12,
            zIndex: 40,
          }}
        >
          <span>{t('⚠️ 无法连接到后端服务，请确保 Python 后端已启动')}</span>
          <button
            onClick={() => setConnectionVisible(false)}
            style={{
              background: 'transparent',
              border: 'none',
              color: '#ffffff',
              cursor: 'pointer',
              fontSize: 11,
              opacity: 0.8,
            }}
          >
            {t('隐藏')}
          </button>
        </div>
      )}

      {/* Main Content Area */}
      <Content
        style={{
          flex: 1,
          overflow: 'hidden',
          background: 'var(--bg-primary)',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <Outlet />
      </Content>

      {/* 设置模态窗 — 宽幅双栏，舒展不挤压 */}
      <Modal
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 15, fontWeight: 700 }}>
            <SettingOutlined style={{ color: 'var(--brand-500)' }} />
            <span>{t('系统全局设置')}</span>
          </div>
        }
        open={settingsDrawerOpen}
        onCancel={() => setSettingsDrawerOpen(false)}
        footer={null}
        width={920}
        centered
        destroyOnHidden={false}
        styles={{
          content: { padding: 0, overflow: 'hidden', borderRadius: 'var(--radius)' },
          header: { padding: '16px 24px', borderBottom: '1px solid var(--border-default)', margin: 0 },
          body: { height: '78vh', padding: 0, overflow: 'hidden', background: 'var(--bg-primary)' },
        }}
      >
        <Settings />
      </Modal>
    </Layout>
  )
}
