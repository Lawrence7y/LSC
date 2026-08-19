import { useState } from 'react'
import { Alert, Col, Row, Select, Slider, Typography } from 'antd'
import type { RecordSettings } from '@/types'
import { useI18n } from '@/i18n'

export type RecordingSpec = Pick<
  RecordSettings,
  | 'encoder'
  | 'crf'
  | 'param_mode'
  | 'bitrate'
  | 'bitrate_unit'
  | 'resolution'
  | 'framerate'
  | 'audio_bitrate'
>

type SchemeId = 'high_default' | 'source_copy' | 'balanced' | 'custom'

export function recordingSpecFromSettings(settings: RecordSettings): RecordingSpec {
  return {
    encoder: settings.encoder,
    crf: settings.crf,
    param_mode: settings.param_mode,
    bitrate: String(settings.bitrate),
    bitrate_unit: settings.bitrate_unit,
    resolution: settings.resolution,
    framerate: settings.framerate,
    audio_bitrate: settings.audio_bitrate,
  }
}

function schemeSpec(scheme: Exclude<SchemeId, 'custom'>, defaults: RecordingSpec): RecordingSpec {
  if (scheme === 'source_copy') {
    return {
      ...defaults,
      encoder: 'copy',
      param_mode: '不限制',
      resolution: '原画',
      framerate: '原画',
    }
  }
  if (scheme === 'balanced') {
    return {
      ...defaults,
      encoder: 'h264_nvenc',
      crf: 23,
      param_mode: 'CRF 质量',
      resolution: '1920:1080',
      framerate: '30',
      audio_bitrate: '128k',
    }
  }
  return { ...defaults }
}

export function RecordingSpecSelector({
  initial,
  onChange,
}: {
  initial: RecordingSpec
  onChange: (spec: RecordingSpec) => void
}) {
  const [scheme, setScheme] = useState<SchemeId>('high_default')
  const [spec, setSpec] = useState<RecordingSpec>(initial)
  const { t } = useI18n()

  const update = <K extends keyof RecordingSpec>(key: K, value: RecordingSpec[K]) => {
    const next = { ...spec, [key]: value }
    setScheme('custom')
    setSpec(next)
    onChange(next)
  }

  const changeScheme = (nextScheme: SchemeId) => {
    if (nextScheme === 'custom') return
    const next = schemeSpec(nextScheme, initial)
    setScheme(nextScheme)
    setSpec(next)
    onChange(next)
  }

  const fieldStyle = { width: '100%' }
  const customRate = spec.param_mode === '自定义码率'
  const isCopy = spec.encoder === 'copy'

  return (
    <div style={{ paddingTop: 8 }}>
      <Alert
        type="info"
        showIcon
        message={t('默认使用“设置 → 录制与编码”中的高端默认规格；本次调整不会修改全局设置。')}
        style={{ marginBottom: 14 }}
      />
      <Row gutter={[12, 12]}>
        <Col span={24}>
          <Typography.Text type="secondary">{t('规格方案')}</Typography.Text>
          <Select
            value={scheme}
            onChange={changeScheme}
            style={fieldStyle}
            options={[
              { value: 'high_default', label: t('高端默认规格（设置中的默认值）') },
              { value: 'source_copy', label: t('原画直拷（体积较大、占用最低）') },
              { value: 'balanced', label: t('1080p 30fps 均衡规格') },
              { value: 'custom', label: t('自定义'), disabled: scheme !== 'custom' },
            ]}
          />
        </Col>
        <Col span={12}>
          <Typography.Text type="secondary">{t('编码器')}</Typography.Text>
          <Select
            value={spec.encoder}
            onChange={(value) => update('encoder', value)}
            style={fieldStyle}
            options={[
              { value: 'h264_nvenc', label: 'H.264 NVIDIA' },
              { value: 'hevc_nvenc', label: 'H.265 NVIDIA' },
              { value: 'h264_qsv', label: 'H.264 Intel' },
              { value: 'h264_amf', label: 'H.264 AMD' },
              { value: 'libx264', label: t('H.264 CPU（兼容）') },
              { value: 'libx265', label: 'H.265 CPU' },
              { value: 'copy', label: t('原画直拷') },
            ]}
          />
        </Col>
        <Col span={12}>
          <Typography.Text type="secondary">{t('编码参数')}</Typography.Text>
          <Select
            value={spec.param_mode}
            disabled={isCopy}
            onChange={(value) => update('param_mode', value)}
            style={fieldStyle}
            options={[
              { value: 'CRF 质量', label: t('CRF 质量') },
              { value: '自定义码率', label: t('自定义码率') },
              { value: '不限制', label: t('不限制') },
            ]}
          />
        </Col>
        <Col span={12}>
          <Typography.Text type="secondary">{t('分辨率')}</Typography.Text>
          <Select
            value={spec.resolution}
            onChange={(value) => update('resolution', value)}
            style={fieldStyle}
            options={[
              { value: '原画', label: t('原画') },
              { value: '1920:1080', label: '1080p' },
              { value: '1280:720', label: '720p' },
              { value: '854:480', label: '480p' },
            ]}
          />
        </Col>
        <Col span={12}>
          <Typography.Text type="secondary">{t('帧率')}</Typography.Text>
          <Select
            value={spec.framerate}
            onChange={(value) => update('framerate', value)}
            style={fieldStyle}
            options={[
              { value: '原画', label: t('原画') },
              { value: '60', label: '60 fps' },
              { value: '30', label: '30 fps' },
              { value: '24', label: '24 fps' },
            ]}
          />
        </Col>
        {!isCopy && !customRate && (
          <Col span={12}>
            <Typography.Text type="secondary">{t('CRF：{crf}', { crf: spec.crf })}</Typography.Text>
            <Slider
              min={18}
              max={28}
              value={spec.crf}
              onChange={(value) => update('crf', value)}
              marks={{ 18: t('高质量'), 23: t('推荐'), 28: t('小体积') }}
            />
          </Col>
        )}
        {!isCopy && customRate && (
          <>
            <Col span={6}>
              <Typography.Text type="secondary">{t('码率单位')}</Typography.Text>
              <Select
                value={spec.bitrate_unit}
                onChange={(value) => update('bitrate_unit', value)}
                style={fieldStyle}
                options={[
                  { value: 'kbps', label: 'kbps' },
                  { value: 'Mbps', label: 'Mbps' },
                ]}
              />
            </Col>
            <Col span={6}>
              <Typography.Text type="secondary">{t('码率')}</Typography.Text>
              <Select
                value={String(spec.bitrate)}
                onChange={(value) => update('bitrate', value)}
                style={fieldStyle}
                options={[2000, 4000, 6000, 8000, 10000, 12000, 15000, 20000].map((value) => ({
                  value: String(value),
                  label: String(value),
                }))}
              />
            </Col>
          </>
        )}
        <Col span={12}>
          <Typography.Text type="secondary">{t('音频')}</Typography.Text>
          <Select
            value={spec.audio_bitrate}
            onChange={(value) => update('audio_bitrate', value)}
            style={fieldStyle}
            options={[
              { value: '128k', label: 'AAC 128k' },
              { value: '192k', label: 'AAC 192k' },
              { value: '256k', label: 'AAC 256k' },
            ]}
          />
        </Col>
      </Row>
    </div>
  )
}
