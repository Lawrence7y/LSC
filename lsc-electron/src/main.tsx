import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
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
document.title = 'LSC - Loading...'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
