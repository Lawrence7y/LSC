import { contextBridge, ipcRenderer } from 'electron'

let _updateStatusCallback: ((status: any) => void) | null = null
let _updateStatusWrapper: ((_event: any, status: any) => void) | null = null
// backend-error 改为多注册安全：每次 on 独立 wrapper，返回单条注销函数；
// removeBackendErrorListeners 通过集合全量清除（兼容旧调用方）
const _backendErrorWrappers = new Set<(_event: any, error: any) => void>()
let _appSettingsWrapper: ((_event: any, settings: any) => void) | null = null

export interface AppAPI {
  setAutoLaunch(enabled: boolean): Promise<void>
  getAutoLaunch(): Promise<boolean>
  setMinimizeToTray(enabled: boolean): Promise<void>
  getMinimizeToTray(): Promise<boolean>
  // 设置变化时主进程通知前端（用于启动时从主进程读取持久化值）
  onAppSettingsChange(callback: (settings: { autoLaunch: boolean; minimizeToTray: boolean }) => void): void
}

declare global {
  interface Window {
    app: AppAPI
  }
}

contextBridge.exposeInMainWorld('electronAPI', {
  // 系统相关
  getAppVersion: () => ipcRenderer.invoke('get-app-version'),
  getPlatform: () => process.platform,
  getBackendWsUrl: () => ipcRenderer.invoke('get-backend-ws-url'),
  getBackendWsToken: () => ipcRenderer.invoke('get-backend-ws-token'),
  onBackendReady: (callback: (payload: { url: string }) => void) => {
    const wrapper = (_event: any, payload: { url: string }) => callback(payload)
    ipcRenderer.on('backend-ready', wrapper)
    return () => ipcRenderer.removeListener('backend-ready', wrapper)
  },
  
  // 窗口控制
  minimizeWindow: () => ipcRenderer.invoke('minimize-window'),
  maximizeWindow: () => ipcRenderer.invoke('maximize-window'),
  closeWindow: () => ipcRenderer.invoke('close-window'),
  
  // 文件操作
  selectDirectory: () => ipcRenderer.invoke('select-directory'),
  openPath: (path: string) => ipcRenderer.invoke('open-path', path),
  showItemInFolder: (path: string) => ipcRenderer.invoke('show-item-in-folder', path),

  // 自动更新
  checkForUpdate: () => ipcRenderer.invoke('check-for-update'),
  downloadUpdate: () => ipcRenderer.invoke('download-update'),
  installUpdate: () => ipcRenderer.invoke('install-update'),
  onUpdateStatus: (callback: (status: any) => void) => {
    if (_updateStatusWrapper) {
      ipcRenderer.removeListener('update-status', _updateStatusWrapper)
    }
    _updateStatusWrapper = (_event: any, status: any) => callback(status)
    _updateStatusCallback = callback
    ipcRenderer.on('update-status', _updateStatusWrapper)
  },
  removeUpdateStatusListeners: () => {
    if (_updateStatusWrapper) {
      ipcRenderer.removeListener('update-status', _updateStatusWrapper)
      _updateStatusWrapper = null
      _updateStatusCallback = null
    }
  },

  // 系统通知
  showNotification: (payload: { title: string; body: string; silent?: boolean }) =>
    ipcRenderer.invoke('show-notification', payload),
  setProgressBar: (progress: number) =>
    ipcRenderer.invoke('set-progress-bar', progress),
  setTrayState: (state: 'idle' | 'recording' | 'error') =>
    ipcRenderer.invoke('set-tray-state', state),
  getBackendError: () =>
    ipcRenderer.invoke('get-backend-error'),
  onBackendError: (callback: (error: string) => void) => {
    const wrapper = (_event: any, error: any) => callback(error)
    _backendErrorWrappers.add(wrapper)
    ipcRenderer.on('backend-error', wrapper)
    // 返回单条注销函数：调用方卸载时只注销自己，不误删其他模块的监听
    return () => {
      _backendErrorWrappers.delete(wrapper)
      ipcRenderer.removeListener('backend-error', wrapper)
    }
  },
  removeBackendErrorListeners: () => {
    for (const wrapper of _backendErrorWrappers) {
      ipcRenderer.removeListener('backend-error', wrapper)
    }
    _backendErrorWrappers.clear()
  },

  // 日志查看
  readLogFile: (opts: { file: string; lines?: number }) =>
    ipcRenderer.invoke('read-log-file', opts),
  openLogFolder: () =>
    ipcRenderer.invoke('open-log-folder'),

  // 退出清理：主进程通知渲染进程清理所有房间
  onCleanupAllRooms: (callback: () => void) => {
    const handler = () => callback()
    ipcRenderer.on('cleanup-all-rooms', handler)
    return () => ipcRenderer.removeListener('cleanup-all-rooms', handler)
  },

  // 依赖安装管理
  getStartupDependencyState: () => ipcRenderer.invoke('get-startup-dependency-state'),
  onStartupDependencyState: (callback: (state: any) => void) => {
    const wrapper = (_event: any, state: any) => callback(state)
    ipcRenderer.on('startup-dependency-state', wrapper)
    return () => ipcRenderer.removeListener('startup-dependency-state', wrapper)
  },
  checkDependencies: () => ipcRenderer.invoke('check-dependencies'),
  installDependencies: (options?: { includeAi?: boolean }) =>
    ipcRenderer.invoke('install-dependencies', options),
  cancelDependencies: () => ipcRenderer.invoke('cancel-dependencies'),
  onDependencyProgress: (callback: (event: any) => void) => {
    const wrapper = (_event: any, evt: any) => callback(evt)
    ipcRenderer.on('dependency-progress', wrapper)
    return () => ipcRenderer.removeListener('dependency-progress', wrapper)
  },
  onDependenciesMissing: (callback: (result: any) => void) => {
    const wrapper = (_event: any, result: any) => callback(result)
    ipcRenderer.on('dependencies-missing', wrapper)
    return () => ipcRenderer.removeListener('dependencies-missing', wrapper)
  },
})

contextBridge.exposeInMainWorld('app', {
  setAutoLaunch: async (enabled: boolean) => {
    await ipcRenderer.invoke('app:set-auto-launch', enabled)
  },
  getAutoLaunch: () => ipcRenderer.invoke('app:get-auto-launch'),
  setMinimizeToTray: async (enabled: boolean) => {
    await ipcRenderer.invoke('app:set-minimize-to-tray', enabled)
  },
  getMinimizeToTray: () => ipcRenderer.invoke('app:get-minimize-to-tray'),
  removeAppSettingsChangeListeners: () => {
    if (_appSettingsWrapper) {
      ipcRenderer.removeListener('app:settings-changed', _appSettingsWrapper)
      _appSettingsWrapper = null
    }
  },
  onAppSettingsChange: (callback: (settings: { autoLaunch: boolean; minimizeToTray: boolean }) => void) => {
    if (_appSettingsWrapper) {
      ipcRenderer.removeListener('app:settings-changed', _appSettingsWrapper)
    }
    _appSettingsWrapper = (_event: any, settings: any) => callback(settings)
    ipcRenderer.on('app:settings-changed', _appSettingsWrapper)
  },
})
