import { useState, useCallback } from 'react'
import { Button, message } from 'antd'
import { ReloadOutlined, FolderOpenOutlined } from '@ant-design/icons'

const MAX_DISPLAY_LINES = 500

type LogFile = 'debug.log' | 'backend.log' | 'backend-stdout.log'

export default function LogViewer() {
  const [content, setContent] = useState('')
  const [logFile, setLogFile] = useState<LogFile>('debug.log')
  const [loading, setLoading] = useState(false)
  const [logPath, setLogPath] = useState('')
  const [size, setSize] = useState(0)

  const fetchLog = useCallback(async (file: string) => {
    setLoading(true)
    try {
      const result = await window.electronAPI?.readLogFile?.({ file, lines: MAX_DISPLAY_LINES })
      if (result?.success) {
        setContent(result.content || '(空)')
        setLogPath(result.path || '')
        setSize(result.size || 0)
      } else {
        message.error(result?.error || '读取日志失败')
      }
    } catch (err) {
      message.error(`读取日志失败: ${err}`)
    } finally {
      setLoading(false)
    }
  }, [])

  const handleOpenFolder = useCallback(async () => {
    const result = await window.electronAPI?.openLogFolder?.()
    if (!result?.success) {
      message.error(result?.error || '打开目录失败')
    }
  }, [])

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <select
          value={logFile}
          onChange={e => {
            const val = e.target.value as LogFile
            setLogFile(val)
            fetchLog(val)
          }}
          className="settings-select"
          style={{ minWidth: 180 }}
        >
          <option value="debug.log">debug.log (主进程)</option>
          <option value="backend.log">backend.log (Python)</option>
          <option value="backend-stdout.log">backend-stdout.log (后端输出)</option>
        </select>
        <Button
          size="small"
          icon={<ReloadOutlined />}
          loading={loading}
          onClick={() => fetchLog(logFile)}
        >
          刷新
        </Button>
        <Button
          size="small"
          icon={<FolderOpenOutlined />}
          onClick={handleOpenFolder}
        >
          打开目录
        </Button>
        {size > 0 && (
          <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
            {(size / 1024).toFixed(0)} KB
          </span>
        )}
      </div>
      <pre style={{
        background: 'var(--background-900)',
        border: '1px solid var(--border-default)',
        borderRadius: 10,
        padding: 12,
        fontSize: 11,
        fontFamily: 'var(--font-mono)',
        color: 'var(--text-secondary)',
        maxHeight: 400,
        overflow: 'auto',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-all',
        margin: 0,
      }}>
        {content || '点击"刷新"查看日志'}
      </pre>
      {logPath && (
        <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>
          路径: {logPath}
        </div>
      )}
    </div>
  )
}
