import { useEffect, useState, useCallback } from 'react'
import { Card, Select, InputNumber, Space, Button, message, Tooltip } from 'antd'
import { FolderOpenOutlined, SaveOutlined } from '@ant-design/icons'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useAppStore } from '@/store/appStore'
import { RecordSettings as RecordSettingsType } from '@/types'

const { Option } = Select

interface ChipOption<T extends string> {
  value: T
  label: string
  tooltip?: string
}

function ChipGroup<T extends string>({
  options,
  value,
  onChange,
}: {
  options: ChipOption<T>[]
  value: T
  onChange: (value: T) => void
}) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
      {options.map((opt) => (
        <Tooltip key={opt.value} title={opt.tooltip} placement="top">
          <Button
            size="small"
            type={value === opt.value ? 'primary' : 'default'}
            onClick={() => onChange(opt.value)}
            style={{ borderRadius: 14, minWidth: 64 }}
          >
            {opt.label}
          </Button>
        </Tooltip>
      ))}
    </div>
  )
}

const qualityOptions: ChipOption<string>[] = [
  { value: '原画', label: '原画', tooltip: '直接拷贝直播流，不重新编码，画质无损但文件较大' },
  { value: '蓝光', label: '蓝光', tooltip: '优先选择平台最高清晰度，画质最佳' },
  { value: '超清', label: '超清', tooltip: '选择平台提供的超清画质流' },
  { value: '高清', label: '高清', tooltip: '重编码或选择高清分辨率，平衡画质与体积' },
  { value: '流畅', label: '流畅', tooltip: '低码率编码或选择标清流，适合网络条件差时使用' },
]

const encoderOptions: ChipOption<string>[] = [
  {
    value: 'libx264',
    label: 'libx264',
    tooltip: 'H.264 CPU 软编码：兼容性最好，CPU 占用较高',
  },
  {
    value: 'libx265',
    label: 'libx265',
    tooltip: 'H.265 CPU 软编码：文件体积更小，但兼容性较差且 CPU 占用高',
  },
  {
    value: 'copy',
    label: 'copy',
    tooltip: '直接拷贝直播流：速度最快、画质无损，但无法精确剪辑',
  },
  {
    value: 'h264_nvenc',
    label: 'h264_nvenc',
    tooltip: 'NVIDIA GPU 硬编码：CPU 占用低、速度快，需要 NVIDIA 显卡',
  },
  {
    value: 'hevc_nvenc',
    label: 'hevc_nvenc',
    tooltip: 'NVIDIA GPU HEVC 硬编码：文件体积更小，需要 NVIDIA 显卡',
  },
]

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
      {/* 画质预设 */}
      <div style={{ marginBottom: 12 }}>
        <Tooltip title="选择录制使用的直播流清晰度或重编码目标画质">
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 4, display: 'inline-block' }}>
            画质预设
          </div>
        </Tooltip>
        <ChipGroup
          options={qualityOptions}
          value={settings.quality}
          onChange={(v) => update('quality', v)}
        />
      </div>

      {/* 编码器 */}
      <div style={{ marginBottom: 12 }}>
        <Tooltip title="编码器决定使用 CPU 还是 GPU 进行编码，以及输出文件的兼容性和体积">
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 4, display: 'inline-block' }}>
            编码器
          </div>
        </Tooltip>
        <ChipGroup
          options={encoderOptions}
          value={settings.encoder}
          onChange={(v) => update('encoder', v)}
        />
      </div>

      {/* 编码参数 */}
      <div style={{ marginBottom: 12 }}>
        <Tooltip title="CRF 按恒定质量编码（画质优先），码率限制按固定码率编码（大小可控）">
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 4, display: 'inline-block' }}>
            编码参数
          </div>
        </Tooltip>
        <Select
          value={settings.param_mode}
          onChange={(v) => update('param_mode', v)}
          style={{ width: '100%', marginBottom: 8 }}
          size="small"
        >
          <Option value="CRF 质量">CRF 质量 - 值越小质量越高</Option>
          <Option value="码率限制">码率限制 - 固定码率编码</Option>
          <Option value="不限制">不限制 - 不设编码参数上限</Option>
        </Select>

        {settings.param_mode === 'CRF 质量' && settings.encoder !== 'copy' && (
          <Space style={{ width: '100%' }}>
            <Tooltip title="CRF 数值越小画质越好、文件越大；数值越大文件越小、画质越低">
              <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>CRF:</span>
            </Tooltip>
            <InputNumber
              value={settings.crf}
              onChange={(v) => update('crf', v ?? 23)}
              min={0}
              max={51}
              size="small"
              style={{ width: 80 }}
            />
            <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
              (18=高质量 23=默认 28=小体积)
            </span>
          </Space>
        )}

        {settings.param_mode === '码率限制' && settings.encoder !== 'copy' && (
          <Space style={{ width: '100%' }}>
            <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>码率:</span>
            <InputNumber
              value={parseInt(settings.bitrate) || 0}
              onChange={(v) => update('bitrate', String(v ?? 8000))}
              min={100}
              max={100000}
              size="small"
              style={{ width: 100 }}
            />
            <Select
              value={settings.bitrate_unit}
              onChange={(v) => update('bitrate_unit', v)}
              size="small"
              style={{ width: 80 }}
            >
              <Option value="kbps">kbps</Option>
              <Option value="Mbps">Mbps</Option>
            </Select>
          </Space>
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
