/// <reference types="vite/client" />

import type * as React from 'react'

declare module 'react' {
  interface DOMAttributes<T> {
    onPointerEnterCapture?: any
    onPointerLeaveCapture?: any
  }
}
