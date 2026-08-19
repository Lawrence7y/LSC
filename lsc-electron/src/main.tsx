import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { getLocale, setLocale } from './i18n'
import './styles/global.css'

// 全局错误捕获 - 输出到 Electron 主进程控制台
window.addEventListener('error', (e) => {
  console.error('[GLOBAL ERROR]', e.message, e.filename, e.lineno, e.error?.stack)
})
window.addEventListener('unhandledrejection', (e) => {
  console.error('[UNHANDLED REJECTION]', e.reason)
})

// 调试标记：确认 React 已挂载
console.log('[main.tsx] React mounting, root element:', document.getElementById('root'))

// 初始化 html lang 属性与窗口标题（语言偏好可能早于 React 挂载）
try {
  setLocale(getLocale())
} catch {
  // 非浏览器环境忽略
}
document.title = getLocale() === 'zh-CN' ? 'LSC - 直播切片系统' : 'LSC - Live Stream Clipper'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
