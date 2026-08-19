import { useEffect, useState, useCallback } from 'react'
import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import { ConfigProvider, theme, App as AntdApp } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import enUS from 'antd/locale/en_US'
import MainLayout from './components/Layout/MainLayout'
import ErrorBoundary from './components/ErrorBoundary'
import Workbench from './pages/Workbench'
import SplashScreen from './pages/SplashScreen'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useNotifications } from '@/hooks/useNotifications'
import { useAppStore } from '@/store/appStore'
import { useI18n } from '@/i18n'

interface StartupDependencyState {
  phase: 'checking' | 'installing' | 'ready' | 'error'
  result?: {
    python: Record<string, boolean>
    core_ok: boolean
    ai_ok: boolean
    ffmpeg_ok: boolean
    all_ok: boolean
  }
  error?: string
}

function AppContent() {
  useWebSocket()
  useNotifications()

  const [dependencyState, setDependencyState] = useState<StartupDependencyState | null>(null)
  const [dependencyOverlayDismissed, setDependencyOverlayDismissed] = useState(false)
  const [isStoreBuild] = useState(() => window.electronAPI?.isStoreBuild?.() ?? false)

  useEffect(() => {
    const api = (window as any).electronAPI
    let active = true
    const unsubscribe = api?.onStartupDependencyState?.((state: StartupDependencyState) => {
      if (!active) return
      setDependencyState(state)
      if (state.phase === 'installing') setDependencyOverlayDismissed(false)
    })

    api?.getStartupDependencyState?.()
      .then((state: StartupDependencyState) => {
        if (active && state) setDependencyState(state)
      })
      .catch(() => {})

    return () => {
      active = false
      unsubscribe?.()
    }
  }, [])

  const handleReady = useCallback(() => {
    setDependencyOverlayDismissed(true)
  }, [])

  // Microsoft Store 版本已内置全部依赖，不显示“下载依赖/安装依赖”启动页。
  const showDependencyOverlay = !isStoreBuild && !dependencyOverlayDismissed &&
    (dependencyState?.phase === 'installing' || dependencyState?.phase === 'error')

  return (
    <>
      <HashRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/" element={<MainLayout />}>
            <Route index element={<Navigate to="/workbench" replace />} />
            <Route path="workbench" element={<Workbench />} />
          </Route>
        </Routes>
      </HashRouter>
      {showDependencyOverlay && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 10000 }}>
          <SplashScreen
            onReady={handleReady}
            autoCheck={false}
            initialResult={dependencyState?.result}
            initialStatus={dependencyState?.phase}
            initialError={dependencyState?.error}
          />
        </div>
      )}
    </>
  )
}

function App() {
  const appTheme = useAppStore((state) => state.appSettings?.theme ?? 'dark')
  const isDark = appTheme === 'dark'
  const { locale } = useI18n()

  // 窗口标题跟随语言
  useEffect(() => {
    document.title = locale === 'zh-CN' ? 'LSC - 直播切片系统' : 'LSC - Live Stream Clipper'
  }, [locale])

  // 语言偏好上报主进程（托盘 tooltip/菜单跟随语言），并持久化到主进程设置
  useEffect(() => {
    ;(window as any).electronAPI?.setLocale?.(locale)
  }, [locale])

  return (
    <ConfigProvider
      locale={locale === 'zh-CN' ? zhCN : enUS}
      theme={{
        algorithm: isDark ? theme.darkAlgorithm : theme.defaultAlgorithm,
        token: {
          colorPrimary: '#31B3AE',
          colorSuccess: '#34c759',
          colorWarning: '#ff9500',
          colorError: '#ff3b30',
          colorBgContainer: isDark ? '#1c1c1e' : '#ffffff',
          colorBgElevated: isDark ? '#2c2c2e' : '#ffffff',
          colorBgLayout: isDark ? '#000000' : '#f5f6f8',
          colorText: isDark ? '#f5f5f7' : '#1a1d23',
          colorTextSecondary: isDark ? '#8e8e93' : '#6b7280',
          colorBorder: isDark ? '#3a3a3c' : '#e5e7eb',
          borderRadius: 8,
          borderRadiusLG: 14,
          borderRadiusSM: 6,
          fontFamily: "'SF Pro Display', 'PingFang SC', system-ui, -apple-system, sans-serif",
        },
      }}
    >
      <AntdApp>
        <ErrorBoundary>
          <AppContent />
        </ErrorBoundary>
      </AntdApp>
    </ConfigProvider>
  )
}

export default App
