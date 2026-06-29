"use strict";
const electron = require("electron");
electron.contextBridge.exposeInMainWorld("electronAPI", {
  // 系统相关
  getAppVersion: () => electron.ipcRenderer.invoke("get-app-version"),
  getPlatform: () => process.platform,
  // 窗口控制
  minimizeWindow: () => electron.ipcRenderer.invoke("minimize-window"),
  maximizeWindow: () => electron.ipcRenderer.invoke("maximize-window"),
  closeWindow: () => electron.ipcRenderer.invoke("close-window"),
  // 文件操作
  selectDirectory: () => electron.ipcRenderer.invoke("select-directory"),
  openPath: (path) => electron.ipcRenderer.invoke("open-path", path),
  // Python 后端通信
  sendToPython: (channel, data) => {
    electron.ipcRenderer.send("python-message", { channel, data });
  },
  onPythonMessage: (callback) => {
    electron.ipcRenderer.on("python-message", (_event, { channel, data }) => {
      callback(channel, data);
    });
  }
});
