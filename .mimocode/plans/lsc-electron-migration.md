# LSC Electron + React 迁移计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [`) syntax for tracking.

**Goal:** 将 LSC 直播切片系统从 PySide6 迁移到 Electron + Vite + React + TypeScript + Ant Design，Python 后端通过 WebSocket 与前端通信。

**Architecture:** 
- 前端：Electron + Vite + React + TypeScript + Ant Design
- 后端：Python WebSocket 服务器，保留现有核心逻辑（平台适配、录制、导出）
- 通信：WebSocket JSON-RPC 协议

**Tech Stack:** Electron, Vite, React 18, TypeScript, Ant Design 5, WebSocket, Python asyncio

---

## 文件结构

```
lsc-electron/
├── electron/                    # Electron 主进程
│   ├── main.ts                  # Electron 入口
│   ├── preload.ts               # 预加载脚本
│   └── ipc-handlers.ts          # IPC 处理器
├── src/                         # React 渲染进程
│   ├── App.tsx                  # 根组件
│   ├── main.tsx                 # React 入口
│   ├── components/              # 通用组件
│   │   ├── Layout/              # 布局组件
│   │   ├── Timeline/            # 时间线组件
│   │   ├── ControlBar/          # 控制栏
│   │   ├── RoomCard/            # 房间卡片
│   │   ├── ClipList/            # 切片列表
│   │   └── Settings/            # 设置组件
│   ├── pages/                   # 页面
│   │   ├── Dashboard/           # 仪表盘
│   │   ├── Workbench/           # 工作台
│   │   └── Settings/            # 设置页
│   ├── hooks/                   # 自定义 Hooks
│   │   ├── useWebSocket.ts      # WebSocket Hook
│   │   ├── useRoomManager.ts    # 房间管理 Hook
│   │   └── useTimeline.ts       # 时间线 Hook
│   ├── services/                # 服务层
│   │   ├── websocket.ts         # WebSocket 客户端
│   │   └── api.ts               # API 接口
│   ├── stores/                  # 状态管理
│   │   ├── roomStore.ts         # 房间状态
│   │   └── settingsStore.ts     # 设置状态
│   ├── types/                   # TypeScript 类型
│   │   └── index.ts
│   └── styles/                  # 样式
│       └── global.css
├── python-backend/              # Python 后端
│   ├── server.py                # WebSocket 服务器
│   ├── handlers/                # 消息处理器
│   │   ├── room_handler.py      # 房间管理
│   │   ├── record_handler.py    # 录制管理
│   │   └── export_handler.py    # 导出管理
│   └── bridge.py                # 前后端桥接
├── package.json
├── tsconfig.json
├── vite.config.ts
├── electron-builder.json
└── requirements.txt
```

---

## Task 1: 初始化 Electron + Vite + React 项目

**Files:**
- Create: `lsc-electron/package.json`
- Create: `lsc-electron/vite.config.ts`
- Create: `lsc-electron/tsconfig.json`
- Create: `lsc-electron/electron/main.ts`
- Create: `lsc-electron/electron/preload.ts`
- Create: `lsc-electron/src/main.tsx`
- Create: `lsc-electron/src/App.tsx`
- Create: `lsc-electron/src/index.html`

- [ ] **Step 1: 创建项目目录和 package.json**

```json
{
  "name": "lsc-electron",
  "version": "1.0.0",
  "description": "LSC 直播切片系统 - Electron 版本",
  "main": "dist-electron/main.js",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build && electron-builder",
    "preview": "vite preview",
    "electron:dev": "vite --config vite.config.ts",
    "electron:build": "vite build && electron-builder"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.20.0",
    "antd": "^5.12.0",
    "@ant-design/icons": "^5.2.6",
    "zustand": "^4.4.7",
    "dayjs": "^1.11.10"
  },
  "devDependencies": {
    "@types/react": "^18.2.43",
    "@types/react-dom": "^18.2.17",
    "@vitejs/plugin-react": "^4.2.1",
    "electron": "^28.0.0",
    "electron-builder": "^24.9.1",
    "typescript": "^5.3.3",
    "vite": "^5.0.8",
    "vite-plugin-electron": "^0.28.0",
    "vite-plugin-electron-renderer": "^0.14.5"
  },
  "build": {
    "appId": "com.lsc.app",
    "productName": "LSC 直播切片系统",
    "directories": {
      "output": "release"
    },
    "files": [
      "dist-electron",
      "dist"
    ],
    "win": {
      "target": "nsis"
    },
    "nsis": {
      "oneClick": false,
      "allowToChangeInstallationDirectory": true
    }
  }
}
```

- [ ] **Step 2: 创建 Vite 配置**

```typescript
// vite.config.ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import electron from 'vite-plugin-electron'
import electronRenderer from 'vite-plugin-electron-renderer'
import path from 'path'

export default defineConfig({
  plugins: [
    react(),
    electron([
      {
        entry: 'electron/main.ts',
        vite: {
          build: {
            outDir: 'dist-electron',
          },
        },
      },
      {
        entry: 'electron/preload.ts',
        onstart(args) {
          args.reload()
        },
        vite: {
          build: {
            outDir: 'dist-electron',
          },
        },
      },
    ]),
    electronRenderer(),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
})
```

- [ ] **Step 3: 创建 TypeScript 配置**

```json
// tsconfig.json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "baseUrl": ".",
    "paths": {
      "@/*": ["./src/*"]
    }
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

```json
// tsconfig.node.json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true
  },
  "include": ["vite.config.ts", "electron/**/*.ts"]
}
```

- [ ] **Step 4: 创建 Electron 主进程**

```typescript
// electron/main.ts
import { app, BrowserWindow } from 'electron'
import path from 'path'

function createWindow() {
  const mainWindow = new BrowserWindow({
    width: 1520,
    height: 920,
    minWidth: 1360,
    minHeight: 800,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    titleBarStyle: 'hiddenInset',
    show: false,
  })

  // 开发环境加载 Vite 开发服务器
  if (process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL)
    mainWindow.webContents.openDevTools()
  } else {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'))
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow.show()
  })
}

app.whenReady().then(createWindow)

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow()
  }
})
```

- [ ] **Step 5: 创建预加载脚本**

```typescript
// electron/preload.ts
import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('electronAPI', {
  // 系统相关
  getAppVersion: () => ipcRenderer.invoke('get-app-version'),
  getPlatform: () => process.platform,
  
  // 窗口控制
  minimizeWindow: () => ipcRenderer.invoke('minimize-window'),
  maximizeWindow: () => ipcRenderer.invoke('maximize-window'),
  closeWindow: () => ipcRenderer.invoke('close-window'),
  
  // 文件操作
  selectDirectory: () => ipcRenderer.invoke('select-directory'),
  openPath: (path: string) => ipcRenderer.invoke('open-path', path),
  
  // Python 后端通信
  sendToPython: (channel: string, data: any) => {
    ipcRenderer.send('python-message', { channel, data })
  },
  onPythonMessage: (callback: (channel: string, data: any) => void) => {
    ipcRenderer.on('python-message', (_event, { channel, data }) => {
      callback(channel, data)
    })
  },
})
```

- [ ] **Step 6: 创建 React 入口**

```html
<!-- src/index.html -->
<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>LSC - 直播切片系统</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

```tsx
// src/main.tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App'
import './styles/global.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN}>
      <App />
    </ConfigProvider>
  </React.StrictMode>,
)
```

```tsx
// src/App.tsx
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import MainLayout from './components/Layout/MainLayout'
import Dashboard from './pages/Dashboard'
import Workbench from './pages/Workbench'
import Settings from './pages/Settings'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<MainLayout />}>
          <Route index element={<Dashboard />} />
          <Route path="workbench" element={<Workbench />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
```

- [ ] **Step 7: 创建全局样式**

```css
/* src/styles/global.css */
:root {
  --bg-primary: #141414;
  --bg-secondary: #1f1f1f;
  --bg-tertiary: #2a2a2a;
  --text-primary: #f0f0f0;
  --text-secondary: #a0a0a0;
  --text-tertiary: #707070;
  --accent-primary: #f5a623;
  --accent-success: #52c41a;
  --accent-warning: #faad14;
  --accent-error: #ff4d4f;
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  background-color: var(--bg-primary);
  color: var(--text-primary);
  -webkit-font-smoothing: antialiased;
}

#root {
  height: 100vh;
  overflow: hidden;
}

/* 滚动条样式 */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

::-webkit-scrollbar-track {
  background: var(--bg-secondary);
}

::-webkit-scrollbar-thumb {
  background: var(--bg-tertiary);
  border-radius: 4px;
}

::-webkit-scrollbar-thumb:hover {
  background: #444;
}
```

- [ ] **Step 8: 安装依赖并测试运行**

```bash
cd lsc-electron
npm install
npm run dev
```

预期：浏览器打开 http://localhost:5173 显示空白页面

---

## Task 2: 创建 WebSocket 服务层

**Files:**
- Create: `lsc-electron/src/services/websocket.ts`
- Create: `lsc-electron/src/types/index.ts`
- Create: `lsc-electron/src/hooks/useWebSocket.ts`

- [ ] **Step 1: 定义 TypeScript 类型**

```typescript
// src/types/index.ts
// 房间相关
export interface RoomSession {
  room_id: string
  room_url: string
  platform: string
  platform_name: string
  streamer_name: string
  stream_title: string
  is_connecting: boolean
  is_connected: boolean
  is_recording: boolean
  record_output_path: string
  record_started_at: string | null
  record_size_mb: number
  last_error: string
  preview_enabled: boolean
  preview_muted: boolean
  mark_in: number | null
  mark_out: number | null
}

// 切片相关
export interface ClipSegment {
  start: number
  end: number
  label: string
  thumbnail_path: string
}

// 流信息
export interface StreamInfo {
  platform: string
  stream_url: string
  streamer: string
  title: string
  is_live: boolean
  selected_quality: string
}

// 录制设置
export interface RecordSettings {
  output_dir: string
  encoder: string
  crf: number
  param_mode: string
  bitrate: string
  bitrate_unit: string
  quality: string
}

// WebSocket 消息
export interface WSMessage {
  type: string
  data: any
  id?: string
}

// API 响应
export interface ApiResponse<T = any> {
  success: boolean
  data?: T
  error?: string
}
```

- [ ] **Step 2: 创建 WebSocket 客户端**

```typescript
// src/services/websocket.ts
import { WSMessage } from '@/types'

type MessageHandler = (data: any) => void

class WebSocketClient {
  private ws: WebSocket | null = null
  private url: string
  private handlers: Map<string, Set<MessageHandler>> = new Map()
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private isConnected = false

  constructor(url: string = 'ws://localhost:8765') {
    this.url = url
  }

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(this.url)

      this.ws.onopen = () => {
        console.log('WebSocket connected')
        this.isConnected = true
        this.emit('connected', null)
        resolve()
      }

      this.ws.onmessage = (event) => {
        try {
          const message: WSMessage = JSON.parse(event.data)
          this.emit(message.type, message.data)
        } catch (err) {
          console.error('Failed to parse WebSocket message:', err)
        }
      }

      this.ws.onclose = () => {
        console.log('WebSocket disconnected')
        this.isConnected = false
        this.emit('disconnected', null)
        this.scheduleReconnect()
      }

      this.ws.onerror = (error) => {
        console.error('WebSocket error:', error)
        reject(error)
      }
    })
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
    }
    this.reconnectTimer = setTimeout(() => {
      console.log('Attempting to reconnect...')
      this.connect().catch(() => {})
    }, 3000)
  }

  send(type: string, data: any): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.warn('WebSocket not connected, queuing message')
      return
    }
    const message: WSMessage = { type, data }
    this.ws.send(JSON.stringify(message))
  }

  on(event: string, handler: MessageHandler): () => void {
    if (!this.handlers.has(event)) {
      this.handlers.set(event, new Set())
    }
    this.handlers.get(event)!.add(handler)
    
    // 返回取消订阅函数
    return () => {
      this.handlers.get(event)?.delete(handler)
    }
  }

  private emit(event: string, data: any): void {
    this.handlers.get(event)?.forEach(handler => handler(data))
  }

  disconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
    }
    this.ws?.close()
  }

  get connected(): boolean {
    return this.isConnected
  }
}

// 单例
export const wsClient = new WebSocketClient()
```

- [ ] **Step 3: 创建 WebSocket Hook**

```typescript
// src/hooks/useWebSocket.ts
import { useEffect, useState, useCallback } from 'react'
import { wsClient } from '@/services/websocket'

export function useWebSocket() {
  const [isConnected, setIsConnected] = useState(false)

  useEffect(() => {
    // 连接 WebSocket
    wsClient.connect().catch(console.error)

    // 监听连接状态
    const unsub1 = wsClient.on('connected', () => setIsConnected(true))
    const unsub2 = wsClient.on('disconnected', () => setIsConnected(false))

    return () => {
      unsub1()
      unsub2()
      wsClient.disconnect()
    }
  }, [])

  const send = useCallback((type: string, data: any) => {
    wsClient.send(type, data)
  }, [])

  const on = useCallback((event: string, handler: (data: any) => void) => {
    return wsClient.on(event, handler)
  }, [])

  return { isConnected, send, on }
}
```

- [ ] **Step 4: 测试 WebSocket 连接**

在 App.tsx 中添加连接状态显示：

```tsx
// src/App.tsx
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { useWebSocket } from '@/hooks/useWebSocket'
import { Tag } from 'antd'
import MainLayout from './components/Layout/MainLayout'
import Dashboard from './pages/Dashboard'
import Workbench from './pages/Workbench'
import Settings from './pages/Settings'

function App() {
  const { isConnected } = useWebSocket()

  return (
    <BrowserRouter>
      <div style={{ position: 'fixed', top: 10, right: 10, zIndex: 1000 }}>
        <Tag color={isConnected ? 'success' : 'error'}>
          {isConnected ? '已连接' : '未连接'}
        </Tag>
      </div>
      <Routes>
        <Route path="/" element={<MainLayout />}>
          <Route index element={<Dashboard />} />
          <Route path="workbench" element={<Workbench />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
```

---

## Task 3: 创建布局组件

**Files:**
- Create: `lsc-electron/src/components/Layout/MainLayout.tsx`
- Create: `lsc-electron/src/components/Layout/Sidebar.tsx`
- Create: `lsc-electron/src/components/Layout/TitleBar.tsx`

- [ ] **Step 1: 创建主布局**

```tsx
// src/components/Layout/MainLayout.tsx
import { Outlet } from 'react-router-dom'
import { Layout } from 'antd'
import Sidebar from './Sidebar'
import TitleBar from './TitleBar'

const { Content } = Layout

export default function MainLayout() {
  return (
    <Layout style={{ height: '100vh' }}>
      <TitleBar />
      <Layout>
        <Sidebar />
        <Content style={{ overflow: 'auto', background: 'var(--bg-primary)' }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
```

- [ ] **Step 2: 创建侧边栏**

```tsx
// src/components/Layout/Sidebar.tsx
import { useNavigate, useLocation } from 'react-router-dom'
import { Layout, Menu } from 'antd'
import {
  DashboardOutlined,
  DesktopOutlined,
  SettingOutlined,
} from '@ant-design/icons'

const { Sider } = Layout

const menuItems = [
  {
    key: '/',
    icon: <DashboardOutlined />,
    label: '仪表盘',
  },
  {
    key: '/workbench',
    icon: <DesktopOutlined />,
    label: '工作台',
  },
  {
    key: '/settings',
    icon: <SettingOutlined />,
    label: '设置',
  },
]

export default function Sidebar() {
  const navigate = useNavigate()
  const location = useLocation()

  return (
    <Sider
      width={200}
      style={{
        background: 'var(--bg-secondary)',
        borderRight: '1px solid rgba(255,255,255,0.06)',
      }}
    >
      <div style={{ padding: '16px', textAlign: 'center' }}>
        <h2 style={{ color: 'var(--accent-primary)', margin: 0 }}>LSC</h2>
      </div>
      <Menu
        mode="inline"
        selectedKeys={[location.pathname]}
        items={menuItems}
        onClick={({ key }) => navigate(key)}
        style={{
          background: 'transparent',
          borderRight: 'none',
        }}
      />
    </Sider>
  )
}
```

- [ ] **Step 3: 创建标题栏**

```tsx
// src/components/Layout/TitleBar.tsx
import { Layout, Button, Space } from 'antd'
import {
  MinusOutlined,
  BorderOutlined,
  CloseOutlined,
} from '@ant-design/icons'

const { Header } = Layout

export default function TitleBar() {
  const handleMinimize = () => {
    window.electronAPI?.minimizeWindow()
  }

  const handleMaximize = () => {
    window.electronAPI?.maximizeWindow()
  }

  const handleClose = () => {
    window.electronAPI?.closeWindow()
  }

  return (
    <Header
      style={{
        background: 'var(--bg-secondary)',
        borderBottom: '1px solid rgba(255,255,255,0.06)',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: '0 16px',
        height: 40,
        lineHeight: '40px',
        WebkitAppRegion: 'drag',
      }}
    >
      <span style={{ fontWeight: 600 }}>LSC - 直播切片系统</span>
      <Space style={{ WebkitAppRegion: 'no-drag' }}>
        <Button
          type="text"
          icon={<MinusOutlined />}
          onClick={handleMinimize}
          size="small"
        />
        <Button
          type="text"
          icon={<BorderOutlined />}
          onClick={handleMaximize}
          size="small"
        />
        <Button
          type="text"
          icon={<CloseOutlined />}
          onClick={handleClose}
          size="small"
          danger
        />
      </Space>
    </Header>
  )
}
```

---

## Task 4: 创建仪表盘页面

**Files:**
- Create: `lsc-electron/src/pages/Dashboard/index.tsx`
- Create: `lsc-electron/src/pages/Dashboard/RoomStatusList.tsx`
- Create: `lsc-electron/src/pages/Dashboard/RecordingHistory.tsx`
- Create: `lsc-electron/src/pages/Dashboard/StorageBar.tsx`

- [ ] **Step 1: 创建仪表盘主组件**

```tsx
// src/pages/Dashboard/index.tsx
import { Row, Col, Card } from 'antd'
import RoomStatusList from './RoomStatusList'
import RecordingHistory from './RecordingHistory'
import StorageBar from './StorageBar'

export default function Dashboard() {
  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ marginBottom: 24, fontSize: 20, fontWeight: 600 }}>仪表盘</h1>
      
      <Row gutter={[16, 16]}>
        <Col span={12}>
          <Card
            title="房间状态"
            style={{ background: 'var(--bg-secondary)', minHeight: 300 }}
          >
            <RoomStatusList />
          </Card>
        </Col>
        <Col span={12}>
          <Card
            title="最近录制"
            style={{ background: 'var(--bg-secondary)', minHeight: 300 }}
          >
            <RecordingHistory />
          </Card>
        </Col>
      </Row>
      
      <div style={{ marginTop: 16 }}>
        <StorageBar />
      </div>
    </div>
  )
}
```

- [ ] **Step 2: 创建房间状态列表**

```tsx
// src/pages/Dashboard/RoomStatusList.tsx
import { List, Tag, Space } from 'antd'
import { Circle } from 'antd/es/circle'

const statusColors: Record<string, string> = {
  recording: 'success',
  connected: 'processing',
  idle: 'default',
  error: 'error',
}

const statusLabels: Record<string, string> = {
  recording: '录制中',
  connected: '已连接',
  idle: '未连接',
  error: '错误',
}

interface RoomStatus {
  title: string
  platform: string
  status: string
  duration: string
}

export default function RoomStatusList() {
  // TODO: 从 WebSocket 获取数据
  const rooms: RoomStatus[] = [
    { title: '某主播的精彩操作直播', platform: '抖音', status: 'recording', duration: '00:32:28' },
    { title: '另一个主播的直播间', platform: 'B站', status: 'connected', duration: '--' },
    { title: '虎牙直播间', platform: '虎牙', status: 'error', duration: '--' },
  ]

  return (
    <List
      dataSource={rooms}
      renderItem={(room) => (
        <List.Item>
          <Space>
            <Circle
              size={8}
              color={
                room.status === 'recording' ? '#52c41a' :
                room.status === 'connected' ? '#1890ff' :
                room.status === 'error' ? '#ff4d4f' : '#707070'
              }
            />
            <span>{room.title}</span>
            <Tag>{room.platform}</Tag>
            <Tag color={statusColors[room.status]}>
              {statusLabels[room.status]}
            </Tag>
            <span style={{ color: 'var(--text-tertiary)' }}>{room.duration}</span>
          </Space>
        </List.Item>
      )}
    />
  )
}
```

- [ ] **Step 3: 创建录制历史**

```tsx
// src/pages/Dashboard/RecordingHistory.tsx
import { List, Space, Typography } from 'antd'
import { PlayCircleOutlined } from '@ant-design/icons'

const { Text } = Typography

interface HistoryItem {
  title: string
  platform: string
  duration: string
  size: string
  time: string
}

export default function RecordingHistory() {
  // TODO: 从 WebSocket 获取数据
  const history: HistoryItem[] = [
    { title: '某主播的精彩操作', platform: '抖音', duration: '00:45:32', size: '2.1 GB', time: '今天 14:30' },
    { title: 'B站主播游戏直播', platform: 'B站', duration: '01:20:15', size: '3.8 GB', time: '今天 10:15' },
    { title: '虎牙主播日常', platform: '虎牙', duration: '00:30:08', size: '1.4 GB', time: '昨天 22:00' },
  ]

  return (
    <List
      dataSource={history}
      renderItem={(item) => (
        <List.Item style={{ cursor: 'pointer' }}>
          <Space>
            <PlayCircleOutlined style={{ color: 'var(--accent-primary)', fontSize: 18 }} />
            <div>
              <div>{item.title}</div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {item.platform} · {item.duration} · {item.size}
              </Text>
            </div>
          </Space>
          <Text type="secondary" style={{ marginLeft: 'auto' }}>{item.time}</Text>
        </List.Item>
      )}
    />
  )
}
```

- [ ] **Step 4: 创建存储使用条**

```tsx
// src/pages/Dashboard/StorageBar.tsx
import { Card, Progress, Space, Typography } from 'antd'

const { Text } = Typography

export default function StorageBar() {
  // TODO: 从 WebSocket 获取数据
  const used = 12.5
  const total = 50
  const percent = (used / total) * 100

  return (
    <Card style={{ background: 'var(--bg-secondary)' }}>
      <Space>
        <Text>存储使用</Text>
        <Text type="secondary">{used} GB</Text>
        <Progress
          percent={percent}
          showInfo={false}
          style={{ width: 200 }}
          strokeColor="var(--accent-primary)"
        />
        <Text type="secondary">{total} GB</Text>
      </Space>
    </Card>
  )
}
```

---

## Task 5: 创建 Python WebSocket 服务器

**Files:**
- Create: `python-backend/server.py`
- Create: `python-backend/handlers/room_handler.py`
- Create: `python-backend/bridge.py`

- [ ] **Step 1: 创建 WebSocket 服务器**

```python
# python-backend/server.py
import asyncio
import json
import websockets
from typing import Dict, Any, Callable

class LSCWebSocketServer:
    def __init__(self, host: str = 'localhost', port: int = 8765):
        self.host = host
        self.port = port
        self.clients: set = set()
        self.handlers: Dict[str, Callable] = {}
        
    def on(self, message_type: str, handler: Callable):
        """注册消息处理器"""
        self.handlers[message_type] = handler
        
    async def handle_client(self, websocket, path):
        """处理客户端连接"""
        self.clients.add(websocket)
        print(f"Client connected. Total: {len(self.clients)}")
        
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    msg_type = data.get('type')
                    msg_data = data.get('data', {})
                    
                    # 调用对应的处理器
                    if msg_type in self.handlers:
                        result = await self.handlers[msg_type](msg_data)
                        if result:
                            await websocket.send(json.dumps({
                                'type': f'{msg_type}_response',
                                'data': result
                            }))
                    else:
                        print(f"Unknown message type: {msg_type}")
                        
                except json.JSONDecodeError:
                    print(f"Invalid JSON: {message}")
                except Exception as e:
                    print(f"Error handling message: {e}")
                    
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.remove(websocket)
            print(f"Client disconnected. Total: {len(self.clients)}")
    
    async def broadcast(self, message_type: str, data: Any):
        """广播消息给所有客户端"""
        if not self.clients:
            return
            
        message = json.dumps({
            'type': message_type,
            'data': data
        })
        
        await asyncio.gather(
            *[client.send(message) for client in self.clients]
        )
    
    async def start(self):
        """启动服务器"""
        print(f"WebSocket server starting on ws://{self.host}:{self.port}")
        async with websockets.serve(self.handle_client, self.host, self.port):
            await asyncio.Future()  # 永远运行


# 全局服务器实例
server = LSCWebSocketServer()


async def main():
    # 注册处理器
    from handlers.room_handler import register_room_handlers
    register_room_handlers(server)
    
    # 启动服务器
    await server.start()


if __name__ == '__main__':
    asyncio.run(main())
```

- [ ] **Step 2: 创建房间管理处理器**

```python
# python-backend/handlers/room_handler.py
import sys
import os

# 添加 lsc 到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from lsc.gui.multi_room.manager import MultiRoomManager
from lsc.gui.multi_room.session import RoomSession

# 全局房间管理器
manager = MultiRoomManager()


def register_room_handlers(server):
    """注册房间相关的消息处理器"""
    
    @server.on('get_rooms')
    async def handle_get_rooms(data):
        """获取所有房间"""
        rooms = manager.list_rooms()
        return {
            'rooms': [
                {
                    'room_id': r.room_id,
                    'room_url': r.room_url,
                    'platform': r.platform,
                    'platform_name': r.platform_name,
                    'streamer_name': r.streamer_name,
                    'stream_title': r.stream_title,
                    'is_connecting': r.is_connecting,
                    'is_connected': r.is_connected,
                    'is_recording': r.is_recording,
                    'record_output_path': r.record_output_path,
                    'record_size_mb': r.record_size_mb,
                    'last_error': r.last_error,
                    'preview_enabled': r.preview_enabled,
                    'preview_muted': r.preview_muted,
                    'mark_in': r.mark_in,
                    'mark_out': r.mark_out,
                }
                for r in rooms
            ]
        }
    
    @server.on('add_room')
    async def handle_add_room(data):
        """添加房间"""
        url = data.get('url', '')
        if not url:
            return {'error': 'URL is required'}
        
        room = manager.add_room(url)
        if room:
            return {
                'success': True,
                'room_id': room.room_id
            }
        return {'error': 'Failed to add room'}
    
    @server.on('connect_room')
    async def handle_connect_room(data):
        """连接房间"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        
        # 异步连接
        manager.connect_room(room_id)
        return {'success': True}
    
    @server.on('start_recording')
    async def handle_start_recording(data):
        """开始录制"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        
        manager.start_recording(room_id)
        return {'success': True}
    
    @server.on('stop_recording')
    async def handle_stop_recording(data):
        """停止录制"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        
        manager.stop_recording(room_id)
        return {'success': True}
    
    @server.on('remove_room')
    async def handle_remove_room(data):
        """删除房间"""
        room_id = data.get('room_id')
        if not room_id:
            return {'error': 'room_id is required'}
        
        manager.remove_room(room_id)
        return {'success': True}
```

- [ ] **Step 2: 创建桥接模块**

```python
# python-backend/bridge.py
import asyncio
import json
from typing import Any, Callable
from .server import LSCWebSocketServer

class LSCBridge:
    """连接 Python 后端和 WebSocket 服务器的桥接模块"""
    
    def __init__(self):
        self.server = LSCWebSocketServer()
        self._room_manager = None
    
    def set_room_manager(self, manager):
        """设置多房间管理器"""
        self._room_manager = manager
    
    async def broadcast(self, event_type: str, data: Any):
        """向所有客户端广播消息"""
        message = json.dumps({
            'type': event_type,
            'data': data
        })
        for client in self.server.clients:
            try:
                await client.send(message)
            except:
                pass
    
    async def start(self):
        """启动 WebSocket 服务器"""
        await self.server.start()
    
    def stop(self):
        """停止服务器"""
        self.server.stop()

# 全局实例
bridge = LSCBridge()
```

---

## Task 6: 集成测试

**Files:**
- Modify: `python-backend/server.py` (添加启动脚本)
- Modify: `src/App.tsx` (连接 WebSocket)

- [ ] **Step 1: 创建 Python 启动脚本**

```python
# python-backend/start.py
import asyncio
import sys
import os

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lsc.gui.multi_room.manager import MultiRoomManager
from python_backend.bridge import bridge

async def main():
    # 初始化多房间管理器
    manager = MultiRoomManager()
    bridge.set_room_manager(manager)
    
    print("Starting LSC WebSocket server...")
    print("Server will be available at ws://localhost:8765")
    
    try:
        await bridge.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        bridge.stop()

if __name__ == '__main__':
    asyncio.run(main())
```

- [ ] **Step 2: 测试完整流程**

1. 启动 Python 后端：
```bash
cd D:\Project\直播切片多人
python python-backend/start.py
```

2. 启动 Electron 前端：
```bash
cd lsc-electron
npm run dev
```

3. 验证：
   - Electron 窗口打开
   - 显示"已连接"标签
   - 仪表盘页面正常显示

---

## 执行顺序

1. **Task 1**: 初始化项目（30 分钟）
2. **Task 2**: WebSocket 服务层（20 分钟）
3. **Task 3**: 布局组件（30 分钟）
4. **Task 4**: 仪表盘页面（30 分钟）
5. **Task 5**: Python WebSocket 服务器（40 分钟）
6. **Task 6**: 集成测试（20 分钟）

**总计**: 约 3 小时完成基础框架

---

## 后续任务（Phase 2）

完成基础框架后，继续实现：
- 工作台页面（房间卡片网格 + 播放器 + 切片列表）
- 时间线组件（React Canvas 实现）
- 录制设置面板
- 主题系统（深色/浅色）
- 打包发布
