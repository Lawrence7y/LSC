import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import { ConfigProvider, theme, App as AntdApp } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import MainLayout from './components/Layout/MainLayout'
import ErrorBoundary from './components/ErrorBoundary'
import Workbench from './pages/Workbench'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useNotifications } from '@/hooks/useNotifications'
import { useAppStore } from '@/store/appStore'

function AppContent() {
  useWebSocket()
  useNotifications()

  return (
      <HashRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/" element={<MainLayout />}>
            <Route index element={<Navigate to="/workbench" replace />} />
            <Route path="workbench" element={<Workbench />} />
          </Route>
        </Routes>
      </HashRouter>
  )
}

function App() {
  const appTheme = useAppStore((state) => state.appSettings?.theme ?? 'dark')
  const isDark = appTheme === 'dark'

  return (
    <ConfigProvider
      locale={zhCN}
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
