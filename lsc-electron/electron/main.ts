import { app, BrowserWindow, ipcMain, dialog, shell, Tray, Menu, nativeImage, Notification, session } from 'electron'
import path from 'path'
import fs from 'fs'
import https from 'https'
import { spawn, execSync, ChildProcess } from 'child_process'
import { extractBackendWsUrl } from './backendUrl'

// ===== 全局日志持久化 =====
const _MAX_LOG_SIZE = 2 * 1024 * 1024 // 2MB
const _MAX_LOG_BACKUPS = 5

function _rotateLogFile(logFile: string): void {
  try {
    if (!fs.existsSync(logFile)) return
    const stats = fs.statSync(logFile)
    if (stats.size < _MAX_LOG_SIZE) return
    for (let i = _MAX_LOG_BACKUPS; i >= 1; i--) {
      const oldPath = `${logFile}.${i}`
      const newPath = `${logFile}.${i + 1}`
      if (i === _MAX_LOG_BACKUPS) {
        if (fs.existsSync(oldPath)) fs.unlinkSync(oldPath)
      } else {
        if (fs.existsSync(oldPath)) fs.renameSync(oldPath, newPath)
      }
    }
    fs.renameSync(logFile, `${logFile}.1`)
  } catch {
    // 轮转失败不应影响日志写入
  }
}

export function appLog(level: 'INFO' | 'WARN' | 'ERROR', module: string, msg: string): void {
  try {
    const logDir = path.join(app.getPath('userData'), 'logs')
    if (!fs.existsSync(logDir)) {
      fs.mkdirSync(logDir, { recursive: true })
    }
    const logFile = path.join(logDir, 'debug.log')
    const line = `${new Date().toISOString()} [${level}] [${module}] ${msg}\n`
    fs.appendFileSync(logFile, line, 'utf-8')
    if (Math.random() < 0.01) {
      _rotateLogFile(logFile)
    }
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
  return path.join(app.getPath('userData'), 'logs', 'backend-stdout.log')
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
  // 1. 优先使用打包内嵌入式 Python(版本与依赖经过验证,避免系统 Python 缺依赖)
  try {
    const bundled = path.join(process.resourcesPath, 'python', 'python.exe')
    if (fs.existsSync(bundled)) {
      appLog('INFO', 'DetectPython', `使用打包内 Python: ${bundled}`)
      return bundled
    }
  } catch (err) {
    // resourcesPath 在开发模式下可能不可用,忽略
    appLog('WARN', 'DetectPython', `检查打包内 Python 失败: ${err}`)
  }
  // 2. 尝试系统 PATH 中的 python / python3(开发模式或未打包时)
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
  // 3. 尝试 WorkBuddy 管理的 Python（~/.workbuddy/binaries/python/versions/）
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
  return null
}

// 获取打包内的 FFmpeg 目录(开发模式下返回 null,由系统 PATH 兜底)
function getBundledFfmpegDir(): string | null {
  try {
    const dir = path.join(process.resourcesPath, 'ffmpeg')
    if (fs.existsSync(dir) && fs.existsSync(path.join(dir, 'ffmpeg.exe'))) {
      return dir
    }
  } catch (err) {
    appLog('WARN', 'Backend', `检查打包内 FFmpeg 失败: ${err}`)
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
  // 优先把打包内 Python/FFmpeg 目录加入 PATH 头部,使子进程 shutil.which() 能直接定位
  const pathParts: string[] = []
  const bundledFfmpegDir = getBundledFfmpegDir()
  if (bundledFfmpegDir) {
    pathParts.push(bundledFfmpegDir)
    appLog('INFO', 'Backend', `使用打包内 FFmpeg 目录: ${bundledFfmpegDir}`)
  }
  // 打包内 Python 目录也加入 PATH(便于子进程找到 python.exe,以及 PySide6 Qt 插件相对定位)
  const bundledPythonDir = path.dirname(interpreter)
  if (bundledPythonDir && fs.existsSync(bundledPythonDir)) {
    pathParts.push(bundledPythonDir)
  }
  if (process.env.PATH) {
    pathParts.push(process.env.PATH)
  }

  const safeEnv: NodeJS.ProcessEnv = {
    PATH: pathParts.join(path.delimiter),
    USERPROFILE: process.env.USERPROFILE,
    APPDATA: process.env.APPDATA,
    LOCALAPPDATA: process.env.LOCALAPPDATA,
    TEMP: process.env.TEMP,
    TMP: process.env.TMP,
    HOME: process.env.HOME,
    SYSTEMROOT: process.env.SYSTEMROOT,
    PATHEXT: process.env.PATHEXT,
    PYTHONUNBUFFERED: '1',
    LSC_LOG_DIR: path.join(app.getPath('userData'), 'logs'),
  }
  if (process.env.LSC_CONFIG_PATH) {
    safeEnv.LSC_CONFIG_PATH = process.env.LSC_CONFIG_PATH
  }
  // 显式传递打包内 FFmpeg 目录,供 config.py 优先使用
  if (bundledFfmpegDir) {
    safeEnv.LSC_BUNDLED_FFMPEG_DIR = bundledFfmpegDir
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

  // ===== 版本更新检测 =====
  // 检测仓库：https://github.com/Lawrence7y/LSC
  // API：https://api.github.com/repos/Lawrence7y/LSC/releases/latest

  /**
   * 向 GitHub API 发起 HTTPS GET 请求，返回响应体 JSON。
   * timeout=10s，失败时抛出含中文的错误信息方便前端展示。
   */
  function fetchGitHubLatestRelease(): Promise<{
    tag_name: string
    name: string
    html_url: string
    body: string
    assets: Array<{ name: string; browser_download_url: string; size: number }>
  }> {
    return new Promise((resolve, reject) => {
      const options = {
        hostname: 'api.github.com',
        path: '/repos/Lawrence7y/LSC/releases/latest',
        method: 'GET',
        headers: {
          'User-Agent': `LSC-App/${app.getVersion()}`,
          'Accept': 'application/vnd.github+json',
        },
        timeout: 10000,
      }
      const req = https.request(options, (res) => {
        let body = ''
        res.setEncoding('utf-8')
        res.on('data', (chunk) => { body += chunk })
        res.on('end', () => {
          if (res.statusCode === 200) {
            try {
              resolve(JSON.parse(body))
            } catch (e) {
              reject(new Error('GitHub API 返回数据解析失败'))
            }
          } else if (res.statusCode === 403 || res.statusCode === 429) {
            reject(new Error('GitHub API 请求过于频繁，请稍后重试'))
          } else if (res.statusCode === 404) {
            reject(new Error('未找到发布版本（仓库可能尚未发布 Release）'))
          } else {
            reject(new Error(`GitHub API 返回异常状态码: ${res.statusCode}`))
          }
        })
      })
      req.on('timeout', () => {
        req.destroy()
        reject(new Error('检查更新超时（可能是网络问题或 GitHub 访问受限），请稍后重试'))
      })
      req.on('error', (err) => {
        if ((err as NodeJS.ErrnoException).code === 'ENOTFOUND') {
          reject(new Error('无法连接到 GitHub，请检查网络连接'))
        } else {
          reject(new Error(`网络请求失败: ${err.message}`))
        }
      })
      req.end()
    })
  }

  /**
   * 比较两个 semver 版本字符串（去除开头 v）。
   * 返回 1 = remote > local（有新版本），0 = 相同，-1 = remote < local。
   */
  function compareVersions(local: string, remote: string): number {
    const normalize = (v: string) => v.replace(/^v/, '').split('.').map(Number)
    const [la, lb, lc] = normalize(local)
    const [ra, rb, rc] = normalize(remote)
    if (ra !== la) return ra > la ? 1 : -1
    if (rb !== lb) return rb > lb ? 1 : -1
    if (rc !== lc) return rc > lc ? 1 : -1
    return 0
  }

  // 缓存最近一次检查结果，避免短时间内频繁请求 GitHub API
  let _lastUpdateCheck: { time: number; result: object } | null = null
  const _UPDATE_CACHE_MS = 5 * 60 * 1000  // 5 分钟缓存

  ipcMain.handle('check-for-update', async () => {
    appLog('INFO', 'Update', '用户触发检查更新')

    // 通知前端：正在检查
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('update-status', { type: 'checking' })
    }

    // 命中缓存时直接返回
    if (_lastUpdateCheck && Date.now() - _lastUpdateCheck.time < _UPDATE_CACHE_MS) {
      appLog('INFO', 'Update', '使用缓存的更新检查结果')
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('update-status', _lastUpdateCheck.result)
      }
      return { success: true }
    }

    try {
      const release = await fetchGitHubLatestRelease()
      const remoteVersion = release.tag_name.replace(/^v/, '')
      const localVersion = app.getVersion()
      const cmp = compareVersions(localVersion, remoteVersion)

      let statusPayload: object
      if (cmp > 0) {
        // 有新版本
        statusPayload = {
          type: 'available',
          version: remoteVersion,
          releaseUrl: release.html_url,
          releaseNotes: release.body || '',
          assets: release.assets,
        }
        appLog('INFO', 'Update', `发现新版本: v${remoteVersion}（当前 v${localVersion}）`)
      } else {
        // 已是最新
        statusPayload = {
          type: 'not-available',
          version: localVersion,
        }
        appLog('INFO', 'Update', `已是最新版本 v${localVersion}`)
      }

      _lastUpdateCheck = { time: Date.now(), result: statusPayload }
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('update-status', statusPayload)
      }
      return { success: true }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      appLog('ERROR', 'Update', `检查更新失败: ${msg}`)
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('update-status', { type: 'error', message: msg })
      }
      return { success: false, error: msg }
    }
  })

  // 打开 GitHub Release 页面（浏览器下载）
  ipcMain.handle('download-update', async () => {
    appLog('INFO', 'Update', '用户点击下载更新，跳转到 GitHub Release 页面')
    // 使用缓存的 releaseUrl，否则直接跳转仓库 releases 页
    const releaseUrl =
      (_lastUpdateCheck?.result as Record<string, string> | undefined)?.releaseUrl ||
      'https://github.com/Lawrence7y/LSC/releases/latest'
    try {
      const parsed = new URL(releaseUrl)
      if (parsed.protocol !== 'https:') {
        appLog('ERROR', 'Update', `Refused non-HTTPS URL: ${releaseUrl}`)
        return { success: false, error: '仅支持 HTTPS 链接' }
      }
      await shell.openExternal(releaseUrl)
      return { success: true }
    } catch (err) {
      appLog('ERROR', 'Update', `打开 Release 页面失败: ${err}`)
      return { success: false, error: String(err) }
    }
  })

  // 安装更新（保留接口兼容性，提示用户手动安装）
  ipcMain.handle('install-update', () => {
    appLog('INFO', 'Update', 'install-update called（手动下载模式，跳转 GitHub）')
    shell.openExternal('https://github.com/Lawrence7y/LSC/releases/latest').catch(() => {})
  })

  // 系统通知
  ipcMain.handle('show-notification', (_event, payload: {
    title: string
    body: string
    silent?: boolean
  }) => {
    if (!Notification.isSupported()) return
    // 窗口聚焦时跳过系统通知（antd message 已处理）
    if (mainWindow?.isFocused()) return
    const notif = new Notification({
      title: payload.title,
      body: payload.body,
      icon: path.join(__dirname, '../../assets/icon.ico'),
      silent: payload.silent ?? false,
    })
    notif.on('click', () => {
      mainWindow?.show()
      mainWindow?.focus()
    })
    notif.show()
    // 任务栏闪烁
    if (mainWindow && !mainWindow.isFocused()) {
      mainWindow.flashFrame(true)
      mainWindow.once('focus', () => mainWindow.flashFrame(false))
    }
  })

  // 任务栏进度条
  ipcMain.handle('set-progress-bar', (_event, progress: number) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.setProgressBar(progress)
    }
  })

  // 托盘动态状态：通过 tooltip 文字区分状态（图标暂不切换，缺少多状态图标资源）
  const _trayTooltips: Record<string, string> = {
    idle: 'LSC 直播切片系统',
    recording: 'LSC 直播切片系统 — 录制中',
    error: 'LSC 直播切片系统 — 有错误',
  }
  ipcMain.handle('set-tray-state', (_event, state: 'idle' | 'recording' | 'error') => {
    if (!tray) return
    try {
      tray.setToolTip(_trayTooltips[state] || _trayTooltips.idle)
    } catch {
      // ignore
    }
  })

  // backend-error 前端监听桥接
  ipcMain.handle('get-backend-error', () => {
    return pythonDetectError
  })

  // 读取日志文件内容（尾部 N 行）
  ipcMain.handle('read-log-file', (_event, opts: { file: string; lines?: number }) => {
    const logDir = path.join(app.getPath('userData'), 'logs')
    const allowedFiles = ['debug.log', 'backend.log', 'backend-stdout.log']
    const fileName = opts.file || 'debug.log'
    if (!allowedFiles.includes(fileName)) {
      return { success: false, error: '不支持的日志文件', content: '' }
    }
    const logPath = path.join(logDir, fileName)
    try {
      if (!fs.existsSync(logPath)) {
        return { success: true, content: '(日志文件不存在或为空)', path: logPath, size: 0 }
      }
      const stats = fs.statSync(logPath)
      const content = fs.readFileSync(logPath, 'utf-8')
      const allLines = content.split('\n').filter(Boolean)
      const tailLines = opts.lines && opts.lines > 0 ? allLines.slice(-opts.lines) : allLines.slice(-500)
      return { success: true, content: tailLines.join('\n'), path: logPath, size: stats.size }
    } catch (err) {
      return { success: false, error: String(err), content: '' }
    }
  })

  // 在资源管理器中打开日志目录
  ipcMain.handle('open-log-folder', () => {
    const logDir = path.join(app.getPath('userData'), 'logs')
    try {
      if (!fs.existsSync(logDir)) {
        fs.mkdirSync(logDir, { recursive: true })
      }
      shell.openPath(logDir)
      return { success: true }
    } catch (err) {
      return { success: false, error: String(err) }
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

  // S-1: Content-Security-Policy — 限制脚本/样式/连接来源，防止 XSS 加载外部资源
  // 开发模式 Vite 需要 unsafe-inline + unsafe-eval 用于 HMR/React Refresh
  // 生产模式使用严格策略（无 unsafe-eval，script-src 限 self + file:）
  const csp = process.env.VITE_DEV_SERVER_URL
    ? "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; connect-src 'self' ws://localhost:* http://localhost:* ws://127.0.0.1:* http://127.0.0.1:*; img-src 'self' data: blob:; media-src 'self' blob:"
    : "default-src 'self' file:; script-src 'self' file:; style-src 'self' 'unsafe-inline'; connect-src 'self' ws://localhost:* http://localhost:* ws://127.0.0.1:* http://127.0.0.1:*; img-src 'self' data: blob:; media-src 'self' blob:"
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [csp]
      }
    })
  })

  // S-2: 阻止渲染进程导航到非预期 URL（防 XSS 通过 location.href 跳转）
  mainWindow.webContents.on('will-navigate', (event, url) => {
    const devUrl = process.env.VITE_DEV_SERVER_URL
    if (devUrl) {
      try {
        const a = new URL(url)
        const b = new URL(devUrl)
        if (a.origin === b.origin && a.pathname === b.pathname) return
      } catch {}
    }
    event.preventDefault()
    appLog('WARN', 'Security', `Blocked navigation to: ${url}`)
  })

  // S-3: 拦截 window.open()，仅允许在系统浏览器中打开 HTTPS 链接
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://')) {
      shell.openExternal(url)
    }
    appLog('WARN', 'Security', `Blocked window.open to: ${url}`)
    return { action: 'deny' }
  })

  // 始终注册渲染进程日志转发和生命周期日志
  mainWindow.webContents.on('console-message', (_event, level, message) => {
    const levelMap: Record<number, 'INFO' | 'WARN' | 'ERROR'> = {
      0: 'INFO',
      1: 'INFO',
      2: 'WARN',
      3: 'ERROR',
    }
    const logLevel = levelMap[level] || 'INFO'
    appLog(logLevel, 'renderer', message);
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

  if (process.platform === 'win32') {
    app.setAppUserModelId('com.lsc.app')
  }

  // 注册应用设置 IPC（只注册一次）
  registerAppSettingsIpc()

  // 注册窗口 IPC（只注册一次）
  registerWindowIpc()

  // 并行启动后端 + 创建窗口 + 创建托盘
  spawnBackend()
  createWindow()
  createTray()
})

app.on('before-quit', () => {
  appLog('INFO', 'App', '应用即将退出，正在清理全部房间...')

  // 通知渲染进程通过 WebSocket 清理所有房间（停止录制/预览/分析）
  try {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('cleanup-all-rooms')
      appLog('INFO', 'App', '已通知渲染进程清理全部房间')
    }
  } catch (err) {
    appLog('ERROR', 'App', `通知渲染进程清理失败: ${err}`)
  }

  // 给后端 2 秒时间完成清理，然后强杀
  setTimeout(() => {
    killBackend()
  }, 2000)
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
