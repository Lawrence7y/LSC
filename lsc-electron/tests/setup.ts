import '@testing-library/jest-dom'

// 测试断言基于中文 UI 文案：固定 i18n 语言为 zh-CN，
// 避免 happy-dom 默认 en-US 导致 getByText('中文') 失败。
// 必须在组件模块加载前执行（setupFiles 先于测试文件模块图导入）。
try {
  localStorage.setItem('lsc.locale', 'zh-CN')
} catch {
  // localStorage 不可用时忽略
}
