"use client"

import { useSyncExternalStore } from "react"

const noop = () => () => {}

/** True on macOS / iOS (for ⌘ vs Ctrl hints). False during SSR. */
export function useIsMac() {
  return useSyncExternalStore(
    noop,
    () => /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent),
    () => false
  )
}
