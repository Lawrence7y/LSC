import { contextBridge, ipcRenderer } from 'electron'

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
    ipcRenderer.on('update-status', (_event, status) => callback(status))
  },
  removeUpdateStatusListeners: () => {
    ipcRenderer.removeAllListeners('update-status')
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
  onAppSettingsChange: (callback: (settings: { autoLaunch: boolean; minimizeToTray: boolean }) => void) => {
    ipcRenderer.on('app:settings-changed', (_event, settings) => {
      callback(settings)
    })
  },
})
