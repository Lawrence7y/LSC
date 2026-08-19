import { useState, useEffect } from 'react'
import { Modal, Button } from 'antd'
import {
  LinkOutlined,
  VideoCameraOutlined,
  ScissorOutlined,
  ExportOutlined,
} from '@ant-design/icons'
import { useI18n } from '@/i18n'

/** localStorage 键：标记是否已完成/跳过引导 */
const ONBOARDING_KEY = 'lsc.onboarding.done'

interface OnboardingStep {
  icon: React.ReactNode
  title: string
  desc: string
  hint: string
}

/**
 * 首次使用引导。
 *
 * 自包含：首次进入工作台时自动弹出（localStorage 记忆），完成后不再打扰。
 * 采用居中分步卡片而非元素锚点 Tour，避免复杂布局下锚点失效，更稳健。
 */
export function Onboarding() {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [step, setStep] = useState(0)

  const STEPS: OnboardingStep[] = [
    {
      icon: <LinkOutlined />,
      title: t('添加直播间'),
      desc: t('把直播间链接粘贴到顶部输入框，支持抖音、B站、虎牙等多平台，一次最多添加 12 路。'),
      hint: t('多路同步监播，是直播切片的第一步。'),
    },
    {
      icon: <VideoCameraOutlined />,
      title: t('开启预览与录制'),
      desc: t('在房间卡片上开启预览实时观看，点击录制按钮保存原始流。录制是后续切片的前提。'),
      hint: t('快捷键 R 可一键开始/停止录制。'),
    },
    {
      icon: <ScissorOutlined />,
      title: t('标记切片'),
      desc: t('在时间线上用 I / O 设置入点与出点，点击「添加切片」加入列表。也可开启 AI 持续分析自动检出高光回合。'),
      hint: t('空格键播放/暂停，方向键微调播放头。'),
    },
    {
      icon: <ExportOutlined />,
      title: t('确认并导出'),
      desc: t('在切片列表确认边界后导出为竖屏/横屏视频，或一键生成剪映草稿继续精剪。'),
      hint: t('导出完成后可直接打开文件或所在目录。'),
    },
  ]

  useEffect(() => {
    try {
      if (!localStorage.getItem(ONBOARDING_KEY)) {
        setOpen(true)
      }
    } catch {
      // localStorage 不可用时不弹引导，静默降级
    }
  }, [])

  const finish = () => {
    try {
      localStorage.setItem(ONBOARDING_KEY, '1')
    } catch {
      // 忽略持久化失败
    }
    setOpen(false)
  }

  const isLast = step === STEPS.length - 1
  const current = STEPS[step]

  return (
    <Modal
      open={open}
      onCancel={finish}
      footer={null}
      centered
      width={440}
      closable
      maskClosable={false}
      title={null}
    >
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '8px 0 4px', textAlign: 'center' }}>
        <div style={{
          width: 64,
          height: 64,
          borderRadius: 16,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: 30,
          color: '#fff',
          background: 'var(--brand-500, #31B3AE)',
          marginBottom: 18,
        }}>
          {current.icon}
        </div>
        <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 10 }}>
          {current.title}
        </div>
        <div style={{ fontSize: 13, lineHeight: 1.7, color: 'var(--text-secondary)', marginBottom: 8, minHeight: 66 }}>
          {current.desc}
        </div>
        <div style={{ fontSize: 12, color: 'var(--brand-400, #4DC4BF)', marginBottom: 20 }}>
          {current.hint}
        </div>

        {/* 步骤指示点 */}
        <div style={{ display: 'flex', gap: 6, marginBottom: 20 }}>
          {STEPS.map((_, i) => (
            <span
              key={i}
              onClick={() => setStep(i)}
              style={{
                width: i === step ? 20 : 7,
                height: 7,
                borderRadius: 4,
                background: i === step ? 'var(--brand-500, #31B3AE)' : 'var(--bg-tertiary, #3a3a3c)',
                cursor: 'pointer',
                transition: 'all 0.2s ease',
              }}
            />
          ))}
        </div>

        <div style={{ display: 'flex', gap: 10, width: '100%' }}>
          <Button onClick={finish} style={{ flex: 1 }}>
            {t('跳过')}
          </Button>
          {step > 0 && (
            <Button onClick={() => setStep(s => s - 1)} style={{ flex: 1 }}>
              {t('上一步')}
            </Button>
          )}
          <Button
            type="primary"
            onClick={() => (isLast ? finish() : setStep(s => s + 1))}
            style={{ flex: 1.5 }}
          >
            {isLast ? t('开始使用') : t('下一步')}
          </Button>
        </div>
      </div>
    </Modal>
  )
}
