import { Component, ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ERROR BOUNDARY]', error.message, info.componentStack)
  }

  handleReload = () => {
    window.location.reload()
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100vh',
          background: 'var(--bg-primary, #000)',
          color: 'var(--text-50, #f5f5f7)',
          fontFamily: "'SF Pro Display', 'PingFang SC', system-ui, sans-serif",
          gap: 16,
        }}>
          <div style={{ fontSize: 48 }}>:(</div>
          <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0 }}>
            应用遇到问题
          </h2>
          <p style={{ fontSize: 13, color: 'var(--text-400, #8e8e93)', margin: 0, maxWidth: 400, textAlign: 'center' }}>
            页面发生了意外错误。请刷新页面重试，如果问题持续出现请重启应用。
          </p>
          <pre style={{
            fontSize: 11,
            color: 'var(--state-error, #ff453a)',
            background: 'var(--bg-secondary, #1c1c1e)',
            padding: '12px 16px',
            borderRadius: 10,
            maxWidth: 500,
            overflow: 'auto',
            maxHeight: 150,
            margin: 0,
          }}>
            {this.state.error?.message}
          </pre>
          <button
            onClick={this.handleReload}
            style={{
              background: 'var(--brand-500, #31B3AE)',
              color: '#fff',
              border: 'none',
              borderRadius: 10,
              padding: '10px 24px',
              fontSize: 14,
              fontWeight: 500,
              cursor: 'pointer',
              marginTop: 8,
            }}
          >
            刷新页面
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
