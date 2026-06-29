"use strict";
Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
const electron = require("electron");
const path = require("path");
const fs = require("fs");
const child_process = require("child_process");
const BACKEND_WS_URL_RE = /\bWebSocket server (?:ready at|listening on)\s+(ws:\/\/(?:localhost|127\.0\.0\.1):\d+)/i;
function extractBackendWsUrl(output) {
  const match = BACKEND_WS_URL_RE.exec(output);
  return match ? match[1] : null;
}
function appLog(level, module2, msg) {
  try {
    const logDir = path.join(electron.app.getPath("userData"), "logs");
    if (!fs.existsSync(logDir)) {
      fs.mkdirSync(logDir, { recursive: true });
    }
    const logFile = path.join(logDir, "debug.log");
    const line = `${(/* @__PURE__ */ new Date()).toISOString()} [${level}] [${module2}] ${msg}
`;
    fs.appendFileSync(logFile, line, "utf-8");
    if (level === "ERROR") {
      console.error(line.trim());
    } else if (level === "WARN") {
      console.warn(line.trim());
    } else {
      console.log(line.trim());
    }
  } catch (err) {
    console.error("[appLog] 日志写入失败:", err);
  }
}
let backendProcess = null;
let mainWindow = null;
let tray = null;
let backendLogStream = null;
let pythonDetectError = null;
let backendWsUrl = null;
let backendOutputBuffer = "";
let settingsCache = {
  autoLaunch: false,
  minimizeToTray: false
};
function getSettingsFilePath() {
  return path.join(electron.app.getPath("userData"), "app-settings.json");
}
function loadSettings() {
  try {
    const filePath = getSettingsFilePath();
    if (fs.existsSync(filePath)) {
      const content = fs.readFileSync(filePath, "utf-8");
      const parsed = JSON.parse(content);
      appLog("INFO", "Settings", `读取本地设置成功: ${content.trim()}`);
      return {
        autoLaunch: !!parsed.autoLaunch,
        minimizeToTray: !!parsed.minimizeToTray
      };
    }
  } catch (err) {
    appLog("ERROR", "Settings", `读取设置失败: ${err}`);
  }
  return { autoLaunch: false, minimizeToTray: false };
}
function saveSettings(settings) {
  try {
    const content = JSON.stringify(settings, null, 2);
    fs.writeFileSync(getSettingsFilePath(), content, "utf-8");
    appLog("INFO", "Settings", `保存设置到本地成功: ${content.trim()}`);
  } catch (err) {
    appLog("ERROR", "Settings", `写入设置失败: ${err}`);
  }
}
function pushSettingsToRenderer() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("app:settings-changed", settingsCache);
  }
}
function getBackendDir() {
  const devBackend = path.join(__dirname, "../../../python-backend");
  if (fs.existsSync(devBackend)) {
    return devBackend;
  }
  return path.join(process.resourcesPath, "python-backend");
}
function getBackendLogPath() {
  return path.join(electron.app.getPath("userData"), "logs", "backend.log");
}
function writeLog(line) {
  try {
    backendLogStream == null ? void 0 : backendLogStream.write(line);
  } catch (err) {
    console.error("[writeLog] 日志写入失败:", err);
  }
}
function consumeBackendOutput(data) {
  const output = data.toString();
  writeLog(output);
  backendOutputBuffer = (backendOutputBuffer + output).slice(-2048);
  const wsUrl = extractBackendWsUrl(backendOutputBuffer);
  if (wsUrl && backendWsUrl !== wsUrl) {
    backendWsUrl = wsUrl;
    writeLog(`[backend-url-detected] ${wsUrl}
`);
    appLog("INFO", "Backend", `检测到后端 WebSocket URL: ${wsUrl}`);
  }
}
function detectPython() {
  const candidates = process.platform === "win32" ? ["python", "python3"] : ["python3", "python"];
  for (const cmd of candidates) {
    try {
      child_process.execSync(`${cmd} --version`, { stdio: "ignore" });
      return cmd;
    } catch (err) {
      appLog("WARN", "DetectPython", `${cmd} 不可用: ${err}`);
    }
  }
  try {
    const home = process.env.USERPROFILE || process.env.HOME || "";
    if (home) {
      const wbPythonDir = path.join(home, ".workbuddy", "binaries", "python", "versions");
      if (fs.existsSync(wbPythonDir)) {
        const versions = fs.readdirSync(wbPythonDir).filter((d) => /^\d+\.\d+\.\d+$/.test(d)).sort().reverse();
        for (const ver of versions) {
          const p = path.join(wbPythonDir, ver, process.platform === "win32" ? "python.exe" : "python");
          if (fs.existsSync(p)) {
            appLog("INFO", "DetectPython", `使用 WorkBuddy 管理的 Python: ${p}`);
            return p;
          }
        }
      }
    }
  } catch (err) {
    appLog("ERROR", "DetectPython", `检查 WorkBuddy Python 失败: ${err}`);
  }
  try {
    const bundled = path.join(process.resourcesPath, "python", "python.exe");
    if (fs.existsSync(bundled)) {
      return bundled;
    }
  } catch (err) {
    appLog("ERROR", "DetectPython", `检查打包内 Python 失败: ${err}`);
  }
  return null;
}
function spawnBackend() {
  const backendDir = getBackendDir();
  const backendEntry = path.join(backendDir, "main.py");
  const interpreter = detectPython();
  backendWsUrl = null;
  backendOutputBuffer = "";
  appLog("INFO", "Backend", `正在启动后端: interpreter=${interpreter}, entry=${backendEntry}, exists=${fs.existsSync(backendEntry)}`);
  const logDir = path.join(electron.app.getPath("userData"), "logs");
  appLog("INFO", "Backend", `日志目录: ${logDir}`);
  try {
    fs.mkdirSync(logDir, { recursive: true });
  } catch (err) {
    appLog("ERROR", "Backend", `创建日志目录失败: ${err}`);
  }
  try {
    if (backendLogStream) {
      try {
        backendLogStream.end();
      } catch (err) {
        appLog("ERROR", "Backend", `关闭旧日志流失败: ${err}`);
      }
    }
    backendLogStream = fs.createWriteStream(getBackendLogPath(), { flags: "a" });
    backendLogStream.on("error", () => {
      backendLogStream = null;
    });
  } catch (err) {
    appLog("ERROR", "Backend", `创建日志流失败: ${err}`);
    backendLogStream = null;
  }
  if (!interpreter) {
    const msg = "未检测到可用的 Python 解释器，请安装 Python 并加入 PATH，或将嵌入式 Python 放入 extraResources/python 目录";
    pythonDetectError = msg;
    writeLog(`
[spawn-failed] ${msg}
`);
    appLog("ERROR", "Backend", msg);
    return;
  }
  writeLog(`
[spawn] interpreter=${interpreter} entry=${backendEntry} cwd=${backendDir}
`);
  const safeEnv = {
    PATH: process.env.PATH,
    USERPROFILE: process.env.USERPROFILE,
    APPDATA: process.env.APPDATA,
    LOCALAPPDATA: process.env.LOCALAPPDATA,
    TEMP: process.env.TEMP,
    TMP: process.env.TMP,
    HOME: process.env.HOME,
    SYSTEMROOT: process.env.SYSTEMROOT,
    PATHEXT: process.env.PATHEXT,
    PYTHONUNBUFFERED: "1"
  };
  if (process.env.PYTHONPATH) {
    safeEnv.PYTHONPATH = process.env.PYTHONPATH;
  }
  try {
    backendProcess = child_process.spawn(interpreter, [backendEntry], {
      cwd: backendDir,
      env: safeEnv,
      windowsHide: true,
      // Windows 下脱离父进程的受限 token，避免 dev 模式下子进程权限不足
      // 导致写入用户主目录时 WinError 5 拒绝访问
      detached: process.platform === "win32"
    });
  } catch (err) {
    writeLog(`[spawn-failed] ${err}
`);
    return;
  }
  if (backendProcess.stdout) {
    backendProcess.stdout.on("data", consumeBackendOutput);
  }
  if (backendProcess.stderr) {
    backendProcess.stderr.on("data", consumeBackendOutput);
  }
  backendProcess.on("exit", (code, signal) => {
    writeLog(`[backend-exit] code=${code} signal=${signal}
`);
    try {
      backendLogStream == null ? void 0 : backendLogStream.end();
    } catch (err) {
      appLog("ERROR", "Backend", `关闭日志流失败: ${err}`);
    }
    backendLogStream = null;
    backendProcess = null;
    backendWsUrl = null;
    backendOutputBuffer = "";
  });
  backendProcess.on("error", (err) => {
    writeLog(`[backend-error] ${err}
`);
  });
}
function killBackend() {
  if (!backendProcess) {
    return;
  }
  const proc = backendProcess;
  backendProcess = null;
  const pid = proc.pid;
  if (!pid) {
    return;
  }
  try {
    if (process.platform === "win32") {
      try {
        child_process.execSync(`taskkill /T /F /PID ${pid}`, { stdio: "ignore" });
        appLog("INFO", "Backend", `成功终止 Windows 后端进程 tree (PID: ${pid})`);
      } catch (err) {
        appLog("ERROR", "Backend", `taskkill 失败: ${err}`);
      }
    } else {
      try {
        process.kill(pid, "SIGTERM");
        appLog("INFO", "Backend", `发送 SIGTERM 至后端进程 (PID: ${pid})`);
      } catch (err) {
        appLog("ERROR", "Backend", `SIGTERM 失败: ${err}`);
        return;
      }
      const maxAttempts = 30;
      let attempts = 0;
      const checkAlive = () => {
        attempts++;
        try {
          process.kill(pid, 0);
        } catch (err) {
          appLog("INFO", "Backend", `后端进程 (PID: ${pid}) 已退出`);
          return;
        }
        if (attempts >= maxAttempts) {
          try {
            process.kill(pid, "SIGKILL");
            appLog("WARN", "Backend", `超时，强杀进程 (PID: ${pid})`);
          } catch (err) {
            appLog("ERROR", "Backend", `SIGKILL 失败: ${err}`);
          }
          return;
        }
        setTimeout(checkAlive, 100);
      };
      setTimeout(checkAlive, 100);
    }
  } catch (err) {
    appLog("ERROR", "Backend", `终止后端失败: ${err}`);
  }
}
function createTray() {
  let image = electron.nativeImage.createEmpty();
  const iconCandidates = [
    path.join(__dirname, "../../assets/icon.ico"),
    path.join(__dirname, "../../build/icon.png"),
    path.join(__dirname, "../../build/icon.ico"),
    path.join(process.resourcesPath, "icon.png")
  ];
  for (const p of iconCandidates) {
    try {
      if (fs.existsSync(p)) {
        const img = electron.nativeImage.createFromPath(p);
        if (!img.isEmpty()) {
          image = img;
          break;
        }
      }
    } catch (err) {
      appLog("ERROR", "Tray", `加载图标失败: ${p} ${err}`);
    }
  }
  try {
    tray = new electron.Tray(image);
    tray.setToolTip("LSC 直播切片系统");
    const menu = electron.Menu.buildFromTemplate([
      {
        label: "显示",
        click: () => {
          if (mainWindow) {
            mainWindow.show();
            mainWindow.focus();
          }
        }
      },
      {
        label: "退出",
        click: () => {
          if (tray) {
            tray.destroy();
            tray = null;
          }
          electron.app.quit();
        }
      }
    ]);
    tray.setContextMenu(menu);
    tray.on("click", () => {
      if (mainWindow) {
        mainWindow.show();
        mainWindow.focus();
      }
    });
    appLog("INFO", "Tray", "托盘菜单创建成功");
  } catch (err) {
    appLog("ERROR", "Tray", `托盘创建失败: ${err}`);
  }
}
function _isSafePath(p) {
  if (!p || typeof p !== "string") {
    return false;
  }
  const resolved = path.resolve(p);
  const allowedRoots = [
    electron.app.getPath("userData"),
    path.join(electron.app.getPath("home"), "LSC")
  ].map((dir) => path.resolve(dir) + path.sep);
  const norm = (s) => process.platform === "win32" ? s.toLowerCase() : s;
  const isAllowed = allowedRoots.some((root) => norm(resolved + path.sep).startsWith(norm(root)));
  if (!isAllowed) {
    return false;
  }
  const ext = path.extname(resolved).toLowerCase();
  const blockedExts = [".exe", ".bat", ".ps1", ".cmd", ".vbs", ".scr"];
  if (blockedExts.includes(ext)) {
    return false;
  }
  return true;
}
function registerWindowIpc() {
  electron.ipcMain.handle("get-app-version", () => {
    appLog("INFO", "IPC", "获取应用版本");
    return electron.app.getVersion();
  });
  electron.ipcMain.handle("get-backend-ws-url", () => {
    appLog("INFO", "IPC", `获取后端 WebSocket URL: ${backendWsUrl}`);
    return backendWsUrl;
  });
  electron.ipcMain.handle("minimize-window", () => {
    appLog("INFO", "IPC", "最小化窗口");
    mainWindow == null ? void 0 : mainWindow.minimize();
  });
  electron.ipcMain.handle("maximize-window", () => {
    appLog("INFO", "IPC", "切换最大化/还原");
    if (mainWindow && mainWindow.isMaximized()) {
      mainWindow.unmaximize();
    } else {
      mainWindow == null ? void 0 : mainWindow.maximize();
    }
  });
  electron.ipcMain.handle("close-window", () => {
    appLog("INFO", "IPC", "关闭窗口");
    mainWindow == null ? void 0 : mainWindow.close();
  });
  electron.ipcMain.handle("select-directory", async () => {
    appLog("INFO", "IPC", "打开选择文件夹对话框");
    if (!mainWindow) return null;
    const result = await electron.dialog.showOpenDialog(mainWindow, {
      properties: ["openDirectory"]
    });
    appLog("INFO", "IPC", `文件夹选择结果: ${result.filePaths[0] || "已取消"}`);
    return result.canceled ? null : result.filePaths[0];
  });
  electron.ipcMain.handle("open-path", async (_event, openPathStr) => {
    appLog("INFO", "IPC", `请求打开路径: ${openPathStr}`);
    if (!_isSafePath(openPathStr)) {
      writeLog(`[open-path-rejected] ${openPathStr}
`);
      appLog("WARN", "IPC", `打开路径被拒绝 (不安全): ${openPathStr}`);
      return { success: false, error: "不允许打开此类型文件" };
    }
    const errMsg = await electron.shell.openPath(openPathStr);
    if (errMsg) {
      writeLog(`[open-path-failed] ${openPathStr} ${errMsg}
`);
      appLog("ERROR", "IPC", `打开路径失败: ${openPathStr} ${errMsg}`);
      return { success: false, error: errMsg };
    }
    appLog("INFO", "IPC", `成功打开路径: ${openPathStr}`);
    return { success: true };
  });
  electron.ipcMain.handle("show-item-in-folder", async (_event, filePath) => {
    appLog("INFO", "IPC", `请求在目录中显示文件: ${filePath}`);
    if (!_isSafePath(filePath)) {
      writeLog(`[show-item-in-folder-rejected] ${filePath}
`);
      appLog("WARN", "IPC", `在目录中显示文件被拒绝: ${filePath}`);
      return { success: false, error: "不允许打开此路径" };
    }
    try {
      electron.shell.showItemInFolder(filePath);
      appLog("INFO", "IPC", `成功在目录中显示文件: ${filePath}`);
      return { success: true };
    } catch (e) {
      writeLog(`[show-item-in-folder-failed] ${filePath} ${e}
`);
      appLog("ERROR", "IPC", `在目录中显示文件失败: ${filePath} ${e}`);
      return { success: false, error: String(e) };
    }
  });
}
function createWindow() {
  mainWindow = new electron.BrowserWindow({
    width: 1520,
    height: 920,
    minWidth: 1360,
    minHeight: 800,
    icon: path.join(__dirname, "../../assets/icon.ico"),
    webPreferences: {
      preload: path.join(__dirname, "../preload/preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false
      // 禁用后台节流，确保预览/录制计时器在窗口非活跃时仍精确运行
    },
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "default",
    show: false
  });
  mainWindow.webContents.on("console-message", (_event, _level, message) => {
    appLog("INFO", "renderer", message);
  });
  mainWindow.webContents.on("did-fail-load", (_event, errorCode, errorDescription, validatedURL) => {
    appLog("ERROR", "createWindow", `LOAD FAILED: code=${errorCode} desc=${errorDescription} url=${validatedURL}`);
  });
  mainWindow.webContents.on("crashed", () => {
    appLog("ERROR", "createWindow", `RENDERER CRASHED`);
  });
  if (process.env.VITE_DEV_SERVER_URL) {
    appLog("INFO", "createWindow", `加载开发服务器: ${process.env.VITE_DEV_SERVER_URL}`);
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
    mainWindow.webContents.openDevTools();
  } else {
    const indexPath = path.join(__dirname, "../../dist/index.html");
    appLog("INFO", "createWindow", `加载生产网页资源: ${indexPath}`);
    mainWindow.loadFile(indexPath).catch((err) => {
      appLog("ERROR", "createWindow", `加载 index.html 失败: ${err.message}`);
    });
  }
  mainWindow.once("ready-to-show", () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.show();
      appLog("INFO", "createWindow", "窗口准备就绪并显示");
    }
  });
  mainWindow.on("close", (e) => {
    if (settingsCache.minimizeToTray && tray && mainWindow && !mainWindow.isDestroyed()) {
      e.preventDefault();
      mainWindow.hide();
      appLog("INFO", "createWindow", "拦截窗口关闭，最小化到系统托盘");
    } else {
      appLog("INFO", "createWindow", "窗口即将关闭并退出进程");
    }
  });
  mainWindow.webContents.once("did-finish-load", () => {
    appLog("INFO", "createWindow", "网页加载完成，推送初始设置");
    pushSettingsToRenderer();
    if (pythonDetectError) {
      mainWindow == null ? void 0 : mainWindow.webContents.send("backend-error", pythonDetectError);
    }
  });
}
function registerAppSettingsIpc() {
  electron.ipcMain.handle("app:set-auto-launch", (_event, enabled) => {
    appLog("INFO", "IPC", `设置开机自启: ${enabled}`);
    settingsCache.autoLaunch = !!enabled;
    try {
      electron.app.setLoginItemSettings({ openAtLogin: !!enabled });
    } catch (err) {
      appLog("ERROR", "IPC", `设置开机启动失败: ${err}`);
    }
    saveSettings(settingsCache);
    pushSettingsToRenderer();
    return settingsCache.autoLaunch;
  });
  electron.ipcMain.handle("app:get-auto-launch", () => {
    return settingsCache.autoLaunch;
  });
  electron.ipcMain.handle("app:set-minimize-to-tray", (_event, enabled) => {
    appLog("INFO", "IPC", `设置最小化到托盘: ${enabled}`);
    settingsCache.minimizeToTray = !!enabled;
    saveSettings(settingsCache);
    pushSettingsToRenderer();
    return settingsCache.minimizeToTray;
  });
  electron.ipcMain.handle("app:get-minimize-to-tray", () => {
    return settingsCache.minimizeToTray;
  });
}
electron.app.commandLine.appendSwitch("autoplay-policy", "no-user-gesture-required");
electron.app.whenReady().then(() => {
  settingsCache = loadSettings();
  try {
    electron.app.setLoginItemSettings({ openAtLogin: settingsCache.autoLaunch });
  } catch (err) {
    appLog("ERROR", "App", `同步开机启动设置失败: ${err}`);
  }
  registerAppSettingsIpc();
  registerWindowIpc();
  spawnBackend();
  createWindow();
  createTray();
});
electron.app.on("before-quit", () => {
  appLog("INFO", "App", "应用即将退出");
  killBackend();
});
process.on("exit", () => {
  killBackend();
});
electron.app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    electron.app.quit();
  }
});
electron.app.on("activate", () => {
  if (electron.BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  } else if (mainWindow) {
    mainWindow.show();
    mainWindow.focus();
  }
});
exports.appLog = appLog;
//# sourceMappingURL=main.js.map
