import { HashRouter, Routes, Route } from 'react-router-dom'
import { ConfigProvider, theme, App as AntdApp } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import MainLayout from './components/Layout/MainLayout'
import Dashboard from './pages/Dashboard'
import Workbench from './pages/Workbench'
import Settings from './pages/Settings'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useAppStore } from '@/store/appStore'

function AppContent() {
  useWebSocket()

  return (
    <HashRouter>
      <Routes>
        <Route path="/" element={<MainLayout />}>
          <Route index element={<Dashboard />} />
          <Route path="workbench" element={<Workbench />} />
          <Route path="settings" element={<Settings />} />
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
          colorPrimary: '#007aff',
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
        <AppContent />
      </AntdApp>
    </ConfigProvider>
  )
}

export default App
