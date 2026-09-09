import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { AddRoomBar } from './AddRoomBar'
import type { RoomUrlValidationState } from '@/hooks/useAddRoom'

const idle: RoomUrlValidationState = { status: 'idle', message: '' }

function setup(overrides: Partial<Parameters<typeof AddRoomBar>[0]> = {}) {
  const props = {
    url: '',
    onUrlChange: vi.fn(),
    onClearValidation: vi.fn(),
    onSubmit: vi.fn(),
    loading: false,
    validation: idle,
    ...overrides,
  }
  render(<AddRoomBar {...props} />)
  return props
}

/**
 * 回归目标：解析逻辑按换行切分多链接，但旧 UI 是单行 Input —— 单行输入框
 * 永远拿不到换行，Onboarding 承诺的「一次最多 12 路」实际不可达。
 */
describe('AddRoomBar 多链接输入', () => {
  it('使用多行文本域，粘贴多行链接才能生效', () => {
    setup()
    const field = screen.getByRole('textbox')
    expect(field.tagName).toBe('TEXTAREA')
    expect(field.getAttribute('placeholder')).toContain('12')
  })

  it('多行输入时按钮显示待添加路数', () => {
    setup({ url: 'https://a\nhttps://b\nhttps://c\n' })
    expect(screen.getByRole('button', { name: /3/ })).toBeTruthy()
  })

  it('超过上限时禁用提交', () => {
    const url = Array.from({ length: 13 }, (_, i) => `https://room${i}`).join('\n')
    setup({ url })
    const submit = screen.getByRole('button') as HTMLButtonElement
    expect(submit.disabled).toBe(true)
  })

  it('空输入时禁用提交', () => {
    setup({ url: '   ' })
    expect((screen.getByRole('button') as HTMLButtonElement).disabled).toBe(true)
  })

  it('回车提交，Shift + 回车留给换行', () => {
    const props = setup({ url: 'https://a' })
    const field = screen.getByRole('textbox')

    fireEvent.keyDown(field, { key: 'Enter', shiftKey: true })
    expect(props.onSubmit).not.toHaveBeenCalled()

    fireEvent.keyDown(field, { key: 'Enter' })
    expect(props.onSubmit).toHaveBeenCalledTimes(1)
  })

  it('重新编辑即清除上一次校验提示', () => {
    const props = setup({
      url: 'https://a',
      validation: { status: 'error', message: '链接格式无效' },
    })
    expect(screen.getByText('链接格式无效')).toBeTruthy()
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'https://b' } })
    expect(props.onClearValidation).toHaveBeenCalledTimes(1)
  })

  it('校验中 / 成功 / 失败三种提示都能可见', () => {
    const { unmount } = render(
      <AddRoomBar
        url=""
        onUrlChange={vi.fn()}
        onClearValidation={vi.fn()}
        onSubmit={vi.fn()}
        loading
        validation={{ status: 'checking', message: '正在验证…' }}
      />,
    )
    expect(screen.getByText('正在验证…')).toBeTruthy()
    unmount()

    const r2 = render(
      <AddRoomBar
        url=""
        onUrlChange={vi.fn()}
        onClearValidation={vi.fn()}
        onSubmit={vi.fn()}
        loading={false}
        validation={{ status: 'success', message: '已添加' }}
      />,
    )
    expect(r2.getByText('已添加')).toBeTruthy()
    r2.unmount()
  })
})
