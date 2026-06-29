"use strict";
const electron = require("electron");
electron.contextBridge.exposeInMainWorld("electronAPI", {
  // 系统相关
  getAppVersion: () => electron.ipcRenderer.invoke("get-app-version"),
  getPlatform: () => process.platform,
  getBackendWsUrl: () => electron.ipcRenderer.invoke("get-backend-ws-url"),
  // 窗口控制
  minimizeWindow: () => electron.ipcRenderer.invoke("minimize-window"),
  maximizeWindow: () => electron.ipcRenderer.invoke("maximize-window"),
  closeWindow: () => electron.ipcRenderer.invoke("close-window"),
  // 文件操作
  selectDirectory: () => electron.ipcRenderer.invoke("select-directory"),
  openPath: (path) => electron.ipcRenderer.invoke("open-path", path),
  // 在资源管理器中高亮定位文件（区别于 openPath 会用默认程序打开文件）
  showItemInFolder: (path) => electron.ipcRenderer.invoke("show-item-in-folder", path)
});
electron.contextBridge.exposeInMainWorld("app", {
  setAutoLaunch: async (enabled) => {
    await electron.ipcRenderer.invoke("app:set-auto-launch", enabled);
  },
  getAutoLaunch: () => electron.ipcRenderer.invoke("app:get-auto-launch"),
  setMinimizeToTray: async (enabled) => {
    await electron.ipcRenderer.invoke("app:set-minimize-to-tray", enabled);
  },
  getMinimizeToTray: () => electron.ipcRenderer.invoke("app:get-minimize-to-tray"),
  onAppSettingsChange: (callback) => {
    electron.ipcRenderer.on("app:settings-changed", (_event, settings) => {
      callback(settings);
    });
  }
});
//# sourceMappingURL=preload.js.map
