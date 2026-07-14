import { useEffect, useState, useCallback } from 'react'
import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import { Layout, Menu, Button, Drawer } from 'antd'
import {
  DesktopOutlined,
  SettingOutlined,
  BulbOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useAppStore } from '@/store/appStore'
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts'
import type { ConnectionStatus } from '@/store/appStore'
import SystemMonitor from './SystemMonitor'
import Settings from '@/pages/Settings'

const { Sider, Content } = Layout

const connectionDotColors: Record<ConnectionStatus, string> = {
  connected: 'var(--state-success)',
  connecting: 'var(--state-warning)',
  disconnected: 'var(--state-error)',
  reconnect_failed: 'var(--state-error)',
}

const connectionLabels: Record<ConnectionStatus, string> = {
  connected: '已连接',
  connecting: '连接中',
  disconnected: '未连接',
  reconnect_failed: '连接失败',
}

const menuItems = [
  {
    key: '/workbench',
    icon: <DesktopOutlined />,
    label: '多房间管理',
  },
  {
    key: '/settings',
    icon: <SettingOutlined />,
    label: '设置',
  },
]

export default function MainLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const { isConnected, send, reconnect } = useWebSocket()
  const connectionStatus = useAppStore((state) => state.connectionStatus)
  const appSettings = useAppStore((state) => state.appSettings)
  const setAppSettings = useAppStore((state) => state.setAppSettings)
  const settings = useAppStore((state) => state.settings)
  const settingsDrawerOpen = useAppStore((state) => state.settingsDrawerOpen)
  const setSettingsDrawerOpen = useAppStore((state) => state.setSettingsDrawerOpen)
  const [connectionVisible, setConnectionVisible] = useState(false)

  // 连接断开时延迟 2 秒再显示 banner，避免 WS 短暂重连期间误报「无法连接到后端」。
  // 连接恢复时立即隐藏 banner。与 Workbench 的 Alert 防抖保持一致。
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

  // 主题切换：更新 store + 实时切换 class + 持久化到后端
  const handleToggleTheme = () => {
    const newTheme = appSettings.theme === 'dark' ? 'light' : 'dark'
    document.documentElement.classList.add('theme-transition')
    setTimeout(() => document.documentElement.classList.remove('theme-transition'), 400)
    if (newTheme === 'dark') {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
    setAppSettings({ theme: newTheme })
    send('save_settings', {
      ...settings,
      appSettings: { ...appSettings, theme: newTheme },
    })
  }

  // 全局页面导航快捷键
  useKeyboardShortcuts(
    [
      { key: '1', ctrl: true, id: 'page:workbench' },
      { key: '2', ctrl: true, id: 'page:settings' },
      { key: 'F5', id: 'page:reload' },
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
    <Layout style={{ height: '100vh', overflow: 'hidden' }}>
      <Sider
        width={200}
        style={{
          background: 'var(--background-800)',
          borderRight: '1px solid var(--border-default)',
          height: '100vh',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {/* Logo */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '20px 16px 24px',
          borderBottom: '1px solid var(--border-default)',
        }}>
          <img 
            src="./assets/logo.png" 
            alt="LSC Logo" 
            style={{
              width: 32,
              height: 32,
              borderRadius: 8,
              objectFit: 'cover',
              flexShrink: 0,
            }} 
          />
          <div>
            <div style={{
              fontSize: 15,
              fontWeight: 700,
              letterSpacing: '0.02em',
              color: 'var(--text-50)',
              lineHeight: 1,
            }}>LSC</div>
            <div style={{
              fontSize: 10,
              fontWeight: 400,
              color: 'var(--text-400)',
              marginTop: 2,
              letterSpacing: '0.01em',
            }}>Live Stream Clipper</div>
          </div>
        </div>

        {/* Navigation */}
        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={({ key }) => {
            if (key === '/settings') {
              setSettingsDrawerOpen(true)
            } else {
              navigate(key)
            }
          }}
          style={{
            flex: 1,
            background: 'transparent',
            borderRight: 'none',
            padding: '12px 0',
          }}
        />

        {/* Footer — 连接状态(4种) + 重连按钮 + 主题切换(底部居中) */}
        <div style={{
          padding: '10px 12px 14px',
          borderTop: '1px solid var(--border-default)',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 8,
        }}>
          {/* 连接状态指示器 (4种状态) */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '4px 10px',
            borderRadius: 6,
            background: 'var(--bg-tertiary)',
            border: '1px solid var(--border-default)',
            width: '100%',
            justifyContent: 'center',
          }}>
            <div style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: connectionDotColors[connectionStatus],
              boxShadow:
                connectionStatus === 'connecting'
                  ? `0 0 0 2px rgba(255, 149, 0, 0.2), 0 0 8px ${connectionDotColors[connectionStatus]}`
                  : `0 0 8px ${connectionDotColors[connectionStatus]}`,
              flexShrink: 0,
            }} />
            <span style={{
              fontSize: 12,
              fontWeight: 500,
              color: 'var(--text-secondary)',
            }}>
              {connectionLabels[connectionStatus]}
            </span>
          </div>
          <SystemMonitor />
          {/* 重连按钮：仅在断开/失败时显示 */}
          {(connectionStatus === 'disconnected' || connectionStatus === 'reconnect_failed') && (
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={reconnect}
              style={{ fontSize: 12 }}
            >
              重新连接
            </Button>
          )}
          {/* 主题切换按钮 — 底部居中 */}
          <Button
            type="text"
            size="small"
            icon={<BulbOutlined />}
            onClick={handleToggleTheme}
            style={{ color: 'var(--text-50)', fontSize: 12, marginTop: 4 }}
          >
            {appSettings.theme === 'dark' ? '浅色' : '深色'}
          </Button>
        </div>
      </Sider>

      <Layout>
        {/* Connection Status Banner */}
        {!isConnected && connectionVisible && (
          <div style={{
            height: 36,
            background: 'var(--state-error)',
            color: 'var(--text-50)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 8,
            fontSize: 13,
          }}>
            <span>⚠️ 无法连接到后端服务，请确保 Python 后端已启动</span>
            <button 
              onClick={() => setConnectionVisible(false)}
              style={{
                background: 'transparent',
                border: 'none',
                color: 'var(--text-50)',
                cursor: 'pointer',
                fontSize: 12,
                opacity: 0.8,
              }}
            >
              隐藏
            </button>
          </div>
        )}

        {/* Content — 问题7：删除了顶部标题和右侧连接状态指示器 */}
        <Content style={{
          flex: 1,
          overflow: 'auto',
          background: 'var(--background-900)',
        }}>
          <Outlet />
        </Content>
      </Layout>

      {/* 设置抽屉 — 侧边弹出，不离开工作台，保持 Workbench 状态 */}
      <Drawer
        title="设置"
        placement="right"
        width={520}
        open={settingsDrawerOpen}
        onClose={() => setSettingsDrawerOpen(false)}
        destroyOnClose={false}
        styles={{
          body: { padding: 0, background: 'var(--background-900)', overflow: 'hidden' },
        }}
      >
        <Settings />
      </Drawer>
    </Layout>
  )
}
