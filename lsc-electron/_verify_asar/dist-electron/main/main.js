"use strict";
const electron = require("electron");
const path = require("path");
const fs = require("fs");
const child_process = require("child_process");
const BACKEND_WS_URL_RE = /\bWebSocket server (?:ready at|listening on)\s+(ws:\/\/(?:localhost|127\.0\.0\.1):\d+)/i;
function extractBackendWsUrl(output) {
  const match = BACKEND_WS_URL_RE.exec(output);
  return match ? match[1] : null;
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
      return {
        autoLaunch: !!parsed.autoLaunch,
        minimizeToTray: !!parsed.minimizeToTray
      };
    }
  } catch (err) {
    console.error("[loadSettings] 读取设置失败:", err);
  }
  return { autoLaunch: false, minimizeToTray: false };
}
function saveSettings(settings) {
  try {
    fs.writeFileSync(getSettingsFilePath(), JSON.stringify(settings, null, 2), "utf-8");
  } catch (err) {
    console.error("[saveSettings] 写入设置失败:", err);
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
    console.log("[spawnBackend] backend WebSocket URL:", wsUrl);
  }
}
function detectPython() {
  const candidates = process.platform === "win32" ? ["python", "python3"] : ["python3", "python"];
  for (const cmd of candidates) {
    try {
      child_process.execSync(`${cmd} --version`, { stdio: "ignore" });
      return cmd;
    } catch (err) {
      console.error(`[detectPython] ${cmd} 不可用:`, err);
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
            console.log(`[detectPython] 使用 WorkBuddy 管理的 Python: ${p}`);
            return p;
          }
        }
      }
    }
  } catch (err) {
    console.error("[detectPython] 检查 WorkBuddy Python 失败:", err);
  }
  try {
    const bundled = path.join(process.resourcesPath, "python", "python.exe");
    if (fs.existsSync(bundled)) {
      return bundled;
    }
  } catch (err) {
    console.error("[detectPython] 检查打包内 Python 失败:", err);
  }
  return null;
}
function spawnBackend() {
  const backendDir = getBackendDir();
  const backendEntry = path.join(backendDir, "main.py");
  const interpreter = detectPython();
  backendWsUrl = null;
  backendOutputBuffer = "";
  console.log("[spawnBackend]", { backendDir, backendEntry, interpreter, exists: fs.existsSync(backendEntry) });
  const logDir = path.join(electron.app.getPath("userData"), "logs");
  console.log("[spawnBackend] userData=", electron.app.getPath("userData"), "logDir=", logDir);
  try {
    fs.mkdirSync(logDir, { recursive: true });
  } catch (err) {
    console.error("[spawnBackend] 创建日志目录失败:", err);
  }
  try {
    if (backendLogStream) {
      try {
        backendLogStream.end();
      } catch (err) {
        console.error("[spawnBackend] 关闭旧日志流失败:", err);
      }
    }
    backendLogStream = fs.createWriteStream(getBackendLogPath(), { flags: "a" });
    backendLogStream.on("error", () => {
      backendLogStream = null;
    });
  } catch (err) {
    console.error("[spawnBackend] 创建日志流失败:", err);
    backendLogStream = null;
  }
  if (!interpreter) {
    const msg = "未检测到可用的 Python 解释器，请安装 Python 并加入 PATH，或将嵌入式 Python 放入 extraResources/python 目录";
    pythonDetectError = msg;
    writeLog(`
[spawn-failed] ${msg}
`);
    console.error("[spawnBackend]", msg);
    return;
  }
  writeLog(`
[spawn] interpreter=${interpreter} entry=${backendEntry} cwd=${backendDir}
`);
  const resourcesDir = path.dirname(backendDir);
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
    PYTHONUNBUFFERED: "1",
    // 设置 PYTHONPATH 包含 resources 目录，使 Python 后端能找到 lsc 包
    PYTHONPATH: [resourcesDir, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter)
  };
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
      console.error("[backend-exit] 关闭日志流失败:", err);
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
      } catch (err) {
        console.error("[killBackend] taskkill 失败:", err);
      }
    } else {
      try {
        process.kill(pid, "SIGTERM");
      } catch (err) {
        console.error("[killBackend] SIGTERM 失败:", err);
        return;
      }
      const maxAttempts = 30;
      let attempts = 0;
      const checkAlive = () => {
        attempts++;
        try {
          process.kill(pid, 0);
        } catch (err) {
          console.error("[killBackend] 进程已退出:", err);
          return;
        }
        if (attempts >= maxAttempts) {
          try {
            process.kill(pid, "SIGKILL");
          } catch (err) {
            console.error("[killBackend] SIGKILL 失败:", err);
          }
          return;
        }
        setTimeout(checkAlive, 100);
      };
      setTimeout(checkAlive, 100);
    }
  } catch (err) {
    console.error("[killBackend] 终止后端失败:", err);
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
      console.error("[createTray] 加载图标失败:", p, err);
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
  } catch (err) {
    console.error("[createTray] 托盘创建失败:", err);
  }
}
function _isSafePath(p) {
  if (!p || typeof p !== "string") {
    return false;
  }
  const resolved = path.resolve(p);
  const allowedRoots = [
    electron.app.getPath("userData"),
    electron.app.getPath("home"),
    electron.app.getPath("videos"),
    electron.app.getPath("desktop"),
    electron.app.getPath("documents"),
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
  electron.ipcMain.handle("get-app-version", () => electron.app.getVersion());
  electron.ipcMain.handle("get-backend-ws-url", () => backendWsUrl);
  electron.ipcMain.handle("minimize-window", () => {
    mainWindow == null ? void 0 : mainWindow.minimize();
  });
  electron.ipcMain.handle("maximize-window", () => {
    if (mainWindow && mainWindow.isMaximized()) {
      mainWindow.unmaximize();
    } else {
      mainWindow == null ? void 0 : mainWindow.maximize();
    }
  });
  electron.ipcMain.handle("close-window", () => {
    mainWindow == null ? void 0 : mainWindow.close();
  });
  electron.ipcMain.handle("select-directory", async () => {
    if (!mainWindow) return null;
    const result = await electron.dialog.showOpenDialog(mainWindow, {
      properties: ["openDirectory"]
    });
    return result.canceled ? null : result.filePaths[0];
  });
  electron.ipcMain.handle("open-path", async (_event, openPathStr) => {
    if (!_isSafePath(openPathStr)) {
      writeLog(`[open-path-rejected] ${openPathStr}
`);
      return { success: false, error: "不允许打开此类型文件" };
    }
    const errMsg = await electron.shell.openPath(openPathStr);
    if (errMsg) {
      writeLog(`[open-path-failed] ${openPathStr} ${errMsg}
`);
      return { success: false, error: errMsg };
    }
    return { success: true };
  });
  electron.ipcMain.handle("show-item-in-folder", async (_event, filePath) => {
    if (!_isSafePath(filePath)) {
      writeLog(`[show-item-in-folder-rejected] ${filePath}
`);
      return { success: false, error: "不允许打开此路径" };
    }
    try {
      electron.shell.showItemInFolder(filePath);
      return { success: true };
    } catch (e) {
      writeLog(`[show-item-in-folder-failed] ${filePath} ${e}
`);
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
  if (process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
    mainWindow.webContents.openDevTools();
  } else {
    const isPackaged = electron.app.isPackaged;
    const indexPath = path.join(__dirname, "../../dist/index.html");
    const debugLogPath = path.join(electron.app.getPath("userData"), "logs", "debug.log");
    const logDir = path.dirname(debugLogPath);
    if (!fs.existsSync(logDir)) {
      fs.mkdirSync(logDir, { recursive: true });
    }
    const debugLog = (msg) => {
      const line = `${(/* @__PURE__ */ new Date()).toISOString()} ${msg}
`;
      fs.appendFileSync(debugLogPath, line);
      console.log(msg);
    };
    debugLog(`[createWindow] isPackaged=${isPackaged}`);
    debugLog(`[createWindow] __dirname=${__dirname}`);
    debugLog(`[createWindow] indexPath=${indexPath}`);
    debugLog(`[createWindow] indexPath exists=${fs.existsSync(indexPath)}`);
    mainWindow.webContents.on("console-message", (_event, _level, message) => {
      debugLog(`[renderer] ${message}`);
    });
    mainWindow.webContents.on("did-fail-load", (_event, errorCode, errorDescription, validatedURL) => {
      debugLog(`[createWindow] LOAD FAILED: code=${errorCode} desc=${errorDescription} url=${validatedURL}`);
    });
    mainWindow.webContents.on("did-finish-load", () => {
      debugLog(`[createWindow] did-finish-load OK`);
    });
    mainWindow.webContents.on("crashed", () => {
      debugLog(`[createWindow] RENDERER CRASHED`);
    });
    mainWindow.loadFile(indexPath).catch((err) => {
      debugLog(`[createWindow] loadFile FAILED: ${err.message}`);
      electron.dialog.showErrorBox("启动失败", `无法加载应用界面: ${err.message}

请确保所有文件都已正确安装。`);
    });
    mainWindow.webContents.openDevTools();
  }
  mainWindow.once("ready-to-show", () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.show();
      console.log("[createWindow] Window shown successfully");
    }
  });
  setTimeout(() => {
    if (mainWindow && !mainWindow.isVisible() && !mainWindow.isDestroyed()) {
      console.warn("[createWindow] Window not visible after timeout, forcing show");
      try {
        mainWindow.show();
        mainWindow.focus();
      } catch (err) {
        console.error("[createWindow] Error forcing window show:", err);
      }
    }
  }, 5e3);
  mainWindow.on("close", (e) => {
    if (settingsCache.minimizeToTray && tray && mainWindow && !mainWindow.isDestroyed()) {
      e.preventDefault();
      mainWindow.hide();
    }
  });
  mainWindow.webContents.once("did-finish-load", () => {
    pushSettingsToRenderer();
    if (pythonDetectError) {
      mainWindow == null ? void 0 : mainWindow.webContents.send("backend-error", pythonDetectError);
    }
  });
}
function registerAppSettingsIpc() {
  electron.ipcMain.handle("app:set-auto-launch", (_event, enabled) => {
    settingsCache.autoLaunch = !!enabled;
    try {
      electron.app.setLoginItemSettings({ openAtLogin: !!enabled });
    } catch (err) {
      console.error("[set-auto-launch] 设置开机启动失败:", err);
    }
    saveSettings(settingsCache);
    pushSettingsToRenderer();
    return settingsCache.autoLaunch;
  });
  electron.ipcMain.handle("app:get-auto-launch", () => {
    return settingsCache.autoLaunch;
  });
  electron.ipcMain.handle("app:set-minimize-to-tray", (_event, enabled) => {
    settingsCache.minimizeToTray = !!enabled;
    saveSettings(settingsCache);
    pushSettingsToRenderer();
    return settingsCache.minimizeToTray;
  });
  electron.ipcMain.handle("app:get-minimize-to-tray", () => {
    return settingsCache.minimizeToTray;
  });
}
electron.app.whenReady().then(() => {
  settingsCache = loadSettings();
  try {
    electron.app.setLoginItemSettings({ openAtLogin: settingsCache.autoLaunch });
  } catch (err) {
    console.error("[whenReady] 同步开机启动设置失败:", err);
  }
  registerAppSettingsIpc();
  registerWindowIpc();
  spawnBackend();
  createWindow();
  createTray();
});
electron.app.on("before-quit", () => {
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
//# sourceMappingURL=main.js.map
