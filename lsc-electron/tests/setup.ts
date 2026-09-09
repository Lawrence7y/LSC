import '@testing-library/jest-dom'
import { afterEach } from 'vitest'

// 测试断言基于中文 UI 文案：固定 i18n 语言为 zh-CN，
// 避免 happy-dom 默认 en-US 导致 getByText('中文') 失败。
// 必须在组件模块加载前执行（setupFiles 先于测试文件模块图导入）。
try {
  localStorage.setItem('lsc.locale', 'zh-CN')
} catch {
  // localStorage 不可用时忽略
}

// 界面偏好（useUiPref）会写 localStorage：不清理的话，前一个用例点开的
// 筛选项 / 折叠态会泄漏到下一个用例，出现「单独跑通过、全跑失败」的假阳性。
// 这里直接 clear 再恢复语言：happy-dom 的 Storage 不可靠支持键名枚举，
// 按前缀逐个删除会静默失效。
afterEach(() => {
  try {
    localStorage.clear()
    localStorage.setItem('lsc.locale', 'zh-CN')
  } catch {
    // ignore
  }
})
