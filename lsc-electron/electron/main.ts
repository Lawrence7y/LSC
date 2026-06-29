import { app, BrowserWindow, ipcMain, dialog, shell, Tray, Menu, nativeImage } from 'electron'
import path from 'path'
import fs from 'fs'
import { spawn, execSync, ChildProcess } from 'child_process'
import { extractBackendWsUrl } from './backendUrl'

// ===== 全局日志持久化 =====
export function appLog(level: 'INFO' | 'WARN' | 'ERROR', module: string, msg: string): void {
  try {
    const logDir = path.join(app.getPath('userData'), 'logs')
    if (!fs.existsSync(logDir)) {
      fs.mkdirSync(logDir, { recursive: true })
    }
    const logFile = path.join(logDir, 'debug.log')
    const line = `${new Date().toISOString()} [${level}] [${module}] ${msg}\n`
    fs.appendFileSync(logFile, line, 'utf-8')
    if (level === 'ERROR') {
      console.error(line.trim())
    } else if (level === 'WARN') {
      console.warn(line.trim())
    } else {
      console.log(line.trim())
    }
  } catch (err) {
    console.error('[appLog] 日志写入失败:', err)
  }
}


// ===== 模块级状态 =====
let backendProcess: ChildProcess | null = null
let mainWindow: BrowserWindow | null = null
let tray: Tray | null = null
let backendLogStream: fs.WriteStream | null = null
// Python 解释器检测失败时缓存错误信息，待窗口加载完成后通知前端
let pythonDetectError: string | null = null
let backendWsUrl: string | null = null
let backendOutputBuffer = ''

interface AppSettings {
  autoLaunch: boolean
  minimizeToTray: boolean
}

let settingsCache: AppSettings = {
  autoLaunch: false,
  minimizeToTray: false,
}

// ===== 设置持久化 =====

function getSettingsFilePath(): string {
  return path.join(app.getPath('userData'), 'app-settings.json')
}

function loadSettings(): AppSettings {
  try {
    const filePath = getSettingsFilePath()
    if (fs.existsSync(filePath)) {
      const content = fs.readFileSync(filePath, 'utf-8')
      const parsed = JSON.parse(content)
      appLog('INFO', 'Settings', `读取本地设置成功: ${content.trim()}`)
      return {
        autoLaunch: !!parsed.autoLaunch,
        minimizeToTray: !!parsed.minimizeToTray,
      }
    }
  } catch (err) {
    appLog('ERROR', 'Settings', `读取设置失败: ${err}`)
  }
  return { autoLaunch: false, minimizeToTray: false }
}

function saveSettings(settings: AppSettings): void {
  try {
    const content = JSON.stringify(settings, null, 2)
    fs.writeFileSync(getSettingsFilePath(), content, 'utf-8')
    appLog('INFO', 'Settings', `保存设置到本地成功: ${content.trim()}`)
  } catch (err) {
    appLog('ERROR', 'Settings', `写入设置失败: ${err}`)
  }
}

function pushSettingsToRenderer(): void {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('app:settings-changed', settingsCache)
  }
}

// ===== Python 后端管理 =====

function getBackendDir(): string {
  const devBackend = path.join(__dirname, '../../../python-backend')
  if (fs.existsSync(devBackend)) {
    return devBackend
  }
  return path.join(process.resourcesPath, 'python-backend')
}

function getBackendLogPath(): string {
  return path.join(app.getPath('userData'), 'logs', 'backend.log')
}

function writeLog(line: string): void {
  try {
    backendLogStream?.write(line)
  } catch (err) {
    // 使用 console.error 避免递归调用 writeLog
    console.error('[writeLog] 日志写入失败:', err)
  }
}

function consumeBackendOutput(data: Buffer): void {
  const output = data.toString()
  writeLog(output)
  backendOutputBuffer = (backendOutputBuffer + output).slice(-2048)

  const wsUrl = extractBackendWsUrl(backendOutputBuffer)
  if (wsUrl && backendWsUrl !== wsUrl) {
    backendWsUrl = wsUrl
    writeLog(`[backend-url-detected] ${wsUrl}\n`)
    appLog('INFO', 'Backend', `检测到后端 WebSocket URL: ${wsUrl}`)
  }
}

// 检测可用的 Python 解释器，返回命令或可执行文件路径；找不到返回 null
function detectPython(): string | null {
  // 1. 优先尝试系统 PATH 中的 python / python3
  const candidates = process.platform === 'win32' ? ['python', 'python3'] : ['python3', 'python']
  for (const cmd of candidates) {
    try {
      execSync(`${cmd} --version`, { stdio: 'ignore' })
      return cmd
    } catch (err) {
      // 该命令不可用（可能未安装或 Windows 上指向 Microsoft Store 重定向），继续尝试下一个
      appLog('WARN', 'DetectPython', `${cmd} 不可用: ${err}`)
    }
  }
  // 2. 尝试 WorkBuddy 管理的 Python（~/.workbuddy/binaries/python/versions/）
  try {
    const home = process.env.USERPROFILE || process.env.HOME || ''
    if (home) {
      const wbPythonDir = path.join(home, '.workbuddy', 'binaries', 'python', 'versions')
      if (fs.existsSync(wbPythonDir)) {
        const versions = fs.readdirSync(wbPythonDir).filter(d => /^\d+\.\d+\.\d+$/.test(d)).sort().reverse()
        for (const ver of versions) {
          const p = path.join(wbPythonDir, ver, process.platform === 'win32' ? 'python.exe' : 'python')
          if (fs.existsSync(p)) {
            appLog('INFO', 'DetectPython', `使用 WorkBuddy 管理的 Python: ${p}`)
            return p
          }
        }
      }
    }
  } catch (err) {
    appLog('ERROR', 'DetectPython', `检查 WorkBuddy Python 失败: ${err}`)
  }
  // 3. 尝试打包环境携带的嵌入式 Python（extraResources/python/python.exe）
  try {
    const bundled = path.join(process.resourcesPath, 'python', 'python.exe')
    if (fs.existsSync(bundled)) {
      return bundled
    }
  } catch (err) {
    // resourcesPath 在某些环境可能不可用，忽略
    appLog('ERROR', 'DetectPython', `检查打包内 Python 失败: ${err}`)
  }
  return null
}

function spawnBackend(): void {
  const backendDir = getBackendDir()
  const backendEntry = path.join(backendDir, 'main.py')
  const interpreter = detectPython()
  backendWsUrl = null
  backendOutputBuffer = ''

  appLog('INFO', 'Backend', `正在启动后端: interpreter=${interpreter}, entry=${backendEntry}, exists=${fs.existsSync(backendEntry)}`)

  // 确保日志目录存在
  const logDir = path.join(app.getPath('userData'), 'logs')
  appLog('INFO', 'Backend', `日志目录: ${logDir}`)
  try {
    fs.mkdirSync(logDir, { recursive: true })
  } catch (err) {
    // 目录可能已存在，忽略
    appLog('ERROR', 'Backend', `创建日志目录失败: ${err}`)
  }

  // 创建日志写入流（必须监听 error 事件，否则异步打开失败会变成 uncaught exception）
  try {
    // 重复创建前关闭旧流，避免流泄漏与并发写同一文件
    if (backendLogStream) {
      try {
        backendLogStream.end()
      } catch (err) {
        appLog('ERROR', 'Backend', `关闭旧日志流失败: ${err}`)
      }
    }
    backendLogStream = fs.createWriteStream(getBackendLogPath(), { flags: 'a' })
    backendLogStream.on('error', () => {
      // 日志写入失败不应影响后端启动
      backendLogStream = null
    })
  } catch (err) {
    appLog('ERROR', 'Backend', `创建日志流失败: ${err}`)
    backendLogStream = null
  }

  // 检测不到 Python 解释器：记录详细日志并缓存错误信息，待窗口加载完成后通知前端
  if (!interpreter) {
    const msg = '未检测到可用的 Python 解释器，请安装 Python 并加入 PATH，或将嵌入式 Python 放入 extraResources/python 目录'
    pythonDetectError = msg
    writeLog(`\n[spawn-failed] ${msg}\n`)
    appLog('ERROR', 'Backend', msg)
    return
  }

  writeLog(`\n[spawn] interpreter=${interpreter} entry=${backendEntry} cwd=${backendDir}\n`)

  // 环境变量白名单透传，避免传递 NODE_ENV/ELECTRON_* 等不必要变量影响子进程
  const safeEnv: NodeJS.ProcessEnv = {
    PATH: process.env.PATH,
    USERPROFILE: process.env.USERPROFILE,
    APPDATA: process.env.APPDATA,
    LOCALAPPDATA: process.env.LOCALAPPDATA,
    TEMP: process.env.TEMP,
    TMP: process.env.TMP,
    HOME: process.env.HOME,
    SYSTEMROOT: process.env.SYSTEMROOT,
    PATHEXT: process.env.PATHEXT,
    PYTHONUNBUFFERED: '1',
  }
  // PYTHONPATH 如有则透传
  if (process.env.PYTHONPATH) {
    safeEnv.PYTHONPATH = process.env.PYTHONPATH
  }

  try {
    backendProcess = spawn(interpreter, [backendEntry], {
      cwd: backendDir,
      env: safeEnv,
      windowsHide: true,
      // Windows 下脱离父进程的受限 token，避免 dev 模式下子进程权限不足
      // 导致写入用户主目录时 WinError 5 拒绝访问
      detached: process.platform === 'win32',
    })
  } catch (err) {
    writeLog(`[spawn-failed] ${err}\n`)
    return
  }

  // 捕获 stdout/stderr 写入日志
  if (backendProcess.stdout) {
    backendProcess.stdout.on('data', consumeBackendOutput)
  }
  if (backendProcess.stderr) {
    backendProcess.stderr.on('data', consumeBackendOutput)
  }

  // 子进程意外退出时记录退出码
  backendProcess.on('exit', (code, signal) => {
    writeLog(`[backend-exit] code=${code} signal=${signal}\n`)
    try {
      backendLogStream?.end()
    } catch (err) {
      appLog('ERROR', 'Backend', `关闭日志流失败: ${err}`)
    }
    backendLogStream = null
    backendProcess = null
    backendWsUrl = null
    backendOutputBuffer = ''
  })

  backendProcess.on('error', (err) => {
    writeLog(`[backend-error] ${err}\n`)
  })
}

function killBackend(): void {
  if (!backendProcess) {
    return
  }
  const proc = backendProcess
  backendProcess = null
  const pid = proc.pid
  if (!pid) {
    return
  }

  try {
    if (process.platform === 'win32') {
      // Windows：taskkill 强杀整棵树（SIGTERM 在 Windows 不可靠）
      try {
        execSync(`taskkill /T /F /PID ${pid}`, { stdio: 'ignore' })
        appLog('INFO', 'Backend', `成功终止 Windows 后端进程 tree (PID: ${pid})`)
      } catch (err) {
        appLog('ERROR', 'Backend', `taskkill 失败: ${err}`)
      }
    } else {
      // POSIX：先 SIGTERM，再用 setTimeout 异步轮询进程是否存活
      // 最多检测 30 次（间隔 100ms，共 3 秒），超时则 SIGKILL
      // 采用 fire-and-forget 模式：before-quit / exit 钩子不需要等待子进程退出完成，
      // 避免同步忙等待阻塞主进程导致 UI 冻结
      try {
        process.kill(pid, 'SIGTERM')
        appLog('INFO', 'Backend', `发送 SIGTERM 至后端进程 (PID: ${pid})`)
      } catch (err) {
        appLog('ERROR', 'Backend', `SIGTERM 失败: ${err}`)
        return
      }
      const maxAttempts = 30
      let attempts = 0
      const checkAlive = (): void => {
        attempts++
        try {
          process.kill(pid, 0) // 检测进程是否存在
        } catch (err) {
          // 进程已退出
          appLog('INFO', 'Backend', `后端进程 (PID: ${pid}) 已退出`)
          return
        }
        if (attempts >= maxAttempts) {
          // 超时强杀
          try {
            process.kill(pid, 'SIGKILL')
            appLog('WARN', 'Backend', `超时，强杀进程 (PID: ${pid})`)
          } catch (err) {
            appLog('ERROR', 'Backend', `SIGKILL 失败: ${err}`)
          }
          return
        }
        setTimeout(checkAlive, 100)
      }
      setTimeout(checkAlive, 100)
    }
  } catch (err) {
    appLog('ERROR', 'Backend', `终止后端失败: ${err}`)
  }
}

// ===== 托盘 =====

function createTray(): void {
  let image = nativeImage.createEmpty()

  // 尝试多个图标路径兜底
  const iconCandidates = [
    path.join(__dirname, '../../assets/icon.ico'),
    path.join(__dirname, '../../build/icon.png'),
    path.join(__dirname, '../../build/icon.ico'),
    path.join(process.resourcesPath, 'icon.png'),
  ]
  for (const p of iconCandidates) {
    try {
      if (fs.existsSync(p)) {
        const img = nativeImage.createFromPath(p)
        if (!img.isEmpty()) {
          image = img
          break
        }
      }
    } catch (err) {
      appLog('ERROR', 'Tray', `加载图标失败: ${p} ${err}`)
    }
  }

  try {
    tray = new Tray(image)
    tray.setToolTip('LSC 直播切片系统')

    const menu = Menu.buildFromTemplate([
      {
        label: '显示',
        click: () => {
          if (mainWindow) {
            mainWindow.show()
            mainWindow.focus()
          }
        },
      },
      {
        label: '退出',
        click: () => {
          if (tray) {
            tray.destroy()
            tray = null
          }
          app.quit()
        },
      },
    ])
    tray.setContextMenu(menu)

    // 单击托盘图标显示窗口
    tray.on('click', () => {
      if (mainWindow) {
        mainWindow.show()
        mainWindow.focus()
      }
    })
    appLog('INFO', 'Tray', '托盘菜单创建成功')
  } catch (err) {
    appLog('ERROR', 'Tray', `托盘创建失败: ${err}`)
  }
}

// ===== 窗口 =====

// 判断 openPath 目标是否安全：必须在允许目录内且扩展名不在可执行文件黑名单
function _isSafePath(p: string): boolean {
  if (!p || typeof p !== 'string') {
    return false
  }
  const resolved = path.resolve(p)
  // 允许的目录白名单：userData 目录与用户主目录下的 LSC 文件夹（覆盖默认 output_dir ~/LSC/output 及录制产物路径）
  const allowedRoots = [
    app.getPath('userData'),
    path.join(app.getPath('home'), 'LSC'),
  ].map((dir) => path.resolve(dir) + path.sep)
  // Windows 路径大小写不敏感，比较时统一小写；POSIX 保持原样
  const norm = (s: string) => (process.platform === 'win32' ? s.toLowerCase() : s)
  const isAllowed = allowedRoots.some((root) => norm(resolved + path.sep).startsWith(norm(root)))
  if (!isAllowed) {
    return false
  }
  // 扩展名黑名单：拒绝可执行文件类型，防止通过 openPath 触发 RCE
  const ext = path.extname(resolved).toLowerCase()
  const blockedExts = ['.exe', '.bat', '.ps1', '.cmd', '.vbs', '.scr']
  if (blockedExts.includes(ext)) {
    return false
  }
  return true
}

// 注册窗口相关 IPC（只在 whenReady 中调用一次，避免 macOS activate 二次注册触发
// "Attempted to register a second handler" 错误）
function registerWindowIpc(): void {
  ipcMain.handle('get-app-version', () => {
    appLog('INFO', 'IPC', '获取应用版本')
    return app.getVersion()
  })
  ipcMain.handle('get-backend-ws-url', () => {
    appLog('INFO', 'IPC', `获取后端 WebSocket URL: ${backendWsUrl}`)
    return backendWsUrl
  })

  ipcMain.handle('minimize-window', () => {
    appLog('INFO', 'IPC', '最小化窗口')
    mainWindow?.minimize()
  })

  ipcMain.handle('maximize-window', () => {
    appLog('INFO', 'IPC', '切换最大化/还原')
    if (mainWindow && mainWindow.isMaximized()) {
      mainWindow.unmaximize()
    } else {
      mainWindow?.maximize()
    }
  })

  ipcMain.handle('close-window', () => {
    appLog('INFO', 'IPC', '关闭窗口')
    mainWindow?.close()
  })

  ipcMain.handle('select-directory', async () => {
    appLog('INFO', 'IPC', '打开选择文件夹对话框')
    if (!mainWindow) return null
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ['openDirectory']
    })
    appLog('INFO', 'IPC', `文件夹选择结果: ${result.filePaths[0] || '已取消'}`)
    return result.canceled ? null : result.filePaths[0]
  })

  ipcMain.handle('open-path', async (_event, openPathStr) => {
    appLog('INFO', 'IPC', `请求打开路径: ${openPathStr}`);
    // 路径白名单校验，拒绝可执行文件与越界路径
    if (!_isSafePath(openPathStr)) {
      writeLog(`[open-path-rejected] ${openPathStr}\n`);
      appLog('WARN', 'IPC', `打开路径被拒绝 (不安全): ${openPathStr}`);
      return { success: false, error: '不允许打开此类型文件' };
    }
    // shell.openPath 成功返回空字符串，失败返回错误信息
    const errMsg = await shell.openPath(openPathStr);
    if (errMsg) {
      writeLog(`[open-path-failed] ${openPathStr} ${errMsg}\n`);
      appLog('ERROR', 'IPC', `打开路径失败: ${openPathStr} ${errMsg}`);
      return { success: false, error: errMsg };
    }
    appLog('INFO', 'IPC', `成功打开路径: ${openPathStr}`);
    return { success: true };
  })

  // 在资源管理器中高亮定位文件（用于导出产物"打开文件夹"按钮）。
  // 区别于 open-path：openPath 会用默认程序打开 .mp4 文件（启动播放器），
  // showItemInFolder 则在资源管理器中选中并高亮该文件。
  ipcMain.handle('show-item-in-folder', async (_event, filePath) => {
    appLog('INFO', 'IPC', `请求在目录中显示文件: ${filePath}`);
    if (!_isSafePath(filePath)) {
      writeLog(`[show-item-in-folder-rejected] ${filePath}\n`);
      appLog('WARN', 'IPC', `在目录中显示文件被拒绝: ${filePath}`);
      return { success: false, error: '不允许打开此路径' };
    }
    try {
      shell.showItemInFolder(filePath);
      appLog('INFO', 'IPC', `成功在目录中显示文件: ${filePath}`);
      return { success: true };
    } catch (e) {
      writeLog(`[show-item-in-folder-failed] ${filePath} ${e}\n`);
      appLog('ERROR', 'IPC', `在目录中显示文件失败: ${filePath} ${e}`);
      return { success: false, error: String(e) };
    }
  })
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1520,
    height: 920,
    minWidth: 1360,
    minHeight: 800,
    icon: path.join(__dirname, '../../assets/icon.ico'),
    webPreferences: {
      preload: path.join(__dirname, '../preload/preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false, // 禁用后台节流，确保预览/录制计时器在窗口非活跃时仍精确运行
    },
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    show: false,
  });

  // 始终注册渲染进程日志转发和生命周期日志
  mainWindow.webContents.on('console-message', (_event, _level, message) => {
    appLog('INFO', 'renderer', message);
  });

  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
    appLog('ERROR', 'createWindow', `LOAD FAILED: code=${errorCode} desc=${errorDescription} url=${validatedURL}`);
  });

  mainWindow.webContents.on('crashed', () => {
    appLog('ERROR', 'createWindow', `RENDERER CRASHED`);
  });

  if (process.env.VITE_DEV_SERVER_URL) {
    appLog('INFO', 'createWindow', `加载开发服务器: ${process.env.VITE_DEV_SERVER_URL}`);
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
    mainWindow.webContents.openDevTools();
  } else {
    const indexPath = path.join(__dirname, '../../dist/index.html');
    appLog('INFO', 'createWindow', `加载生产网页资源: ${indexPath}`);
    mainWindow.loadFile(indexPath).catch(err => {
      appLog('ERROR', 'createWindow', `加载 index.html 失败: ${err.message}`);
    });
  }

  mainWindow.once('ready-to-show', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.show();
      appLog('INFO', 'createWindow', '窗口准备就绪并显示');
    }
  });

  // 最小化到托盘
  mainWindow.on('close', (e) => {
    if (settingsCache.minimizeToTray && tray && mainWindow && !mainWindow.isDestroyed()) {
      e.preventDefault();
      mainWindow.hide();
      appLog('INFO', 'createWindow', '拦截窗口关闭，最小化到系统托盘');
    } else {
      appLog('INFO', 'createWindow', '窗口即将关闭并退出进程');
    }
  });

  mainWindow.webContents.once('did-finish-load', () => {
    appLog('INFO', 'createWindow', '网页加载完成，推送初始设置');
    pushSettingsToRenderer();
    if (pythonDetectError) {
      mainWindow?.webContents.send('backend-error', pythonDetectError);
    }
  });
}

// ===== 应用设置 IPC =====

function registerAppSettingsIpc(): void {
  ipcMain.handle('app:set-auto-launch', (_event, enabled) => {
    appLog('INFO', 'IPC', `设置开机自启: ${enabled}`);
    settingsCache.autoLaunch = !!enabled;
    try {
      app.setLoginItemSettings({ openAtLogin: !!enabled });
    } catch (err) {
      appLog('ERROR', 'IPC', `设置开机启动失败: ${err}`);
    }
    saveSettings(settingsCache);
    pushSettingsToRenderer();
    return settingsCache.autoLaunch;
  });

  ipcMain.handle('app:get-auto-launch', () => {
    return settingsCache.autoLaunch;
  });

  ipcMain.handle('app:set-minimize-to-tray', (_event, enabled) => {
    appLog('INFO', 'IPC', `设置最小化到托盘: ${enabled}`);
    settingsCache.minimizeToTray = !!enabled;
    saveSettings(settingsCache);
    pushSettingsToRenderer();
    return settingsCache.minimizeToTray;
  });

  ipcMain.handle('app:get-minimize-to-tray', () => {
    return settingsCache.minimizeToTray;
  });
}

// ===== 生命周期 =====

app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required')

app.whenReady().then(() => {
  // 启动时读取 app-settings.json 初始化缓存
  settingsCache = loadSettings()
  // 同步 autoLaunch 状态到系统
  try {
    app.setLoginItemSettings({ openAtLogin: settingsCache.autoLaunch })
  } catch (err) {
    appLog('ERROR', 'App', `同步开机启动设置失败: ${err}`)
  }

  // 注册应用设置 IPC（只注册一次）
  registerAppSettingsIpc()

  // 注册窗口 IPC（只注册一次）
  registerWindowIpc()

  // 启动 Python 后端
  spawnBackend()

  // 创建窗口（保持原有 createWindow 逻辑）
  createWindow()

  // 创建托盘
  createTray()
})

app.on('before-quit', () => {
  appLog('INFO', 'App', '应用即将退出')
  killBackend()
})

process.on('exit', () => {
  killBackend()
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow()
  } else if (mainWindow) {
    mainWindow.show()
    mainWindow.focus()
  }
})
