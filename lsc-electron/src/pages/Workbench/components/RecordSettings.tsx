import { useEffect, useState, useCallback } from 'react'
import { Card, Select, Slider, Space, Button, message } from 'antd'
import { FolderOpenOutlined, SaveOutlined } from '@ant-design/icons'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useAppStore } from '@/store/appStore'
import { RecordSettings as RecordSettingsType } from '@/types'

const { Option } = Select

export function RecordSettings() {
  const { isConnected, send } = useWebSocket()
  const settings = useAppStore((state) => state.settings)
  const setSettings = useAppStore((state) => state.setSettings)
  const appSettings = useAppStore((state) => state.appSettings)
  const [dirty, setDirty] = useState(false)

  // 加载后端设置
  useEffect(() => {
    if (isConnected) {
      send('get_settings', {})
    }
  }, [isConnected, send])

  const update = useCallback(<K extends keyof RecordSettingsType>(key: K, value: RecordSettingsType[K]) => {
    setSettings({ [key]: value })
    setDirty(true)
  }, [setSettings])

  const handleSave = useCallback(() => {
    send('save_settings', { ...settings, appSettings })
    message.success('录制设置已保存')
    setDirty(false)
  }, [send, settings, appSettings])

  const handleBrowse = async () => {
    if (window.electronAPI) {
      const dir = await window.electronAPI.selectDirectory()
      if (dir) {
        update('output_dir', dir)
      }
    } else {
      message.info('请在 Electron 桌面版中使用目录选择功能')
    }
  }

  return (
    <Card
      size="small"
      title={
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>录制设置</span>
          <Button
            type="primary"
            size="small"
            icon={<SaveOutlined />}
            onClick={handleSave}
            disabled={!dirty}
          >
            保存
          </Button>
        </div>
      }
      style={{
        margin: '8px 16px',
        background: 'var(--bg-secondary)',
      }}
    >
      {/* 画质预设（标签和下拉框同一行） */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span style={{ fontSize: 12, color: 'var(--text-tertiary)', flexShrink: 0, width: 36 }}>画质</span>
        <Select
          value={settings.quality}
          onChange={(v) => update('quality', v)}
          style={{ flex: 1 }}
          size="small"
        >
          <Option value="原画">原画（直接拷贝，画质无损）</Option>
          <Option value="蓝光">蓝光（平台最高清晰度）</Option>
          <Option value="超清">超清（超清画质流）</Option>
          <Option value="高清">高清（平衡画质与体积）</Option>
          <Option value="流畅">流畅（低码率，网络差）</Option>
        </Select>
      </div>

      {/* 编码器（标签和下拉框同一行） */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span style={{ fontSize: 12, color: 'var(--text-tertiary)', flexShrink: 0, width: 36 }}>编码器</span>
        <Select
          value={settings.encoder}
          onChange={(v) => update('encoder', v)}
          style={{ flex: 1 }}
          size="small"
        >
          <Option value="libx264">libx264（CPU 软编，兼容好）</Option>
          <Option value="libx265">libx265（CPU HEVC，体积小）</Option>
          <Option value="copy">copy（直接拷贝，最快）</Option>
          <Option value="h264_nvenc">h264_nvenc（NVIDIA GPU）</Option>
          <Option value="hevc_nvenc">hevc_nvenc（NVIDIA HEVC）</Option>
        </Select>
      </div>

      {/* 编码参数 */}
      <div style={{ marginBottom: 10 }}>
        <div style={{ display: 'flex', gap: 8, marginBottom: 4 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 2 }}>编码参数</div>
            <Select
              value={settings.param_mode}
              onChange={(v) => update('param_mode', v)}
              style={{ width: '100%' }}
              size="small"
            >
              <Option value="CRF 质量">CRF 质量</Option>
              <Option value="码率限制">码率限制</Option>
              <Option value="不限制">不限制</Option>
            </Select>
          </div>
          {settings.param_mode === '码率限制' && settings.encoder !== 'copy' && (
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 2 }}>码率</div>
              <div style={{ display: 'flex', gap: 4 }}>
                <Select
                  value={settings.bitrate_unit}
                  onChange={(v) => update('bitrate_unit', v)}
                  style={{ width: 80 }}
                  size="small"
                >
                  <Option value="kbps">kbps</Option>
                  <Option value="Mbps">Mbps</Option>
                </Select>
                <Select
                  value={String(settings.bitrate)}
                  onChange={(v) => update('bitrate', v)}
                  style={{ flex: 1 }}
                  size="small"
                >
                  {[1000, 2000, 4000, 6000, 8000, 10000, 12000, 15000, 20000].map(b => (
                    <Option key={b} value={String(b)}>{b}</Option>
                  ))}
                </Select>
              </div>
            </div>
          )}
        </div>

        {/* CRF Slider */}
        {settings.param_mode === 'CRF 质量' && settings.encoder !== 'copy' && (
          <div style={{ padding: '0 4px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text-tertiary)' }}>
              <span>18（小体积）</span>
              <span style={{ fontWeight: 600, color: 'var(--brand-400)' }}>CRF {settings.crf}</span>
              <span>28（高质量）</span>
            </div>
            <Slider
              min={18}
              max={28}
              value={settings.crf}
              onChange={(v) => update('crf', v)}
              marks={{ 18: '', 23: '23', 28: '' }}
              tooltip={{ open: false }}
              style={{ width: '100%', margin: '4px 0' }}
            />
          </div>
        )}
      </div>

      {/* 输出目录 */}
      <div>
        <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 4 }}>
          输出目录
        </div>
        <Space.Compact style={{ width: '100%' }}>
          <Button
            icon={<FolderOpenOutlined />}
            onClick={handleBrowse}
            size="small"
          >
            选择
          </Button>
          <div style={{
            flex: 1,
            padding: '4px 8px',
            background: 'var(--bg-tertiary)',
            borderRadius: '0 4px 4px 0',
            fontSize: 12,
            color: 'var(--text-secondary)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}>
            {settings.output_dir}
          </div>
        </Space.Compact>
      </div>
    </Card>
  )
}
