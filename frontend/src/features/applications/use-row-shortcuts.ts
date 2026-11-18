import { useEffect, useRef } from "react"

/**
 * Whether a key press belongs to whatever has focus rather than to the page's
 * shortcuts: typing in a field, or moving inside a menu, listbox or popover.
 */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false
  if (target.closest("input, textarea, select, [contenteditable=''], [contenteditable='true']")) return true
  return target.closest("[role='menu'], [role='listbox'], [role='combobox'], [data-slot='popover-content']") !== null
}

/** The id after (`step` 1) or before (`step` -1) `current`, clamped to the list; the first id when none is active. */
export function adjacentId(ids: readonly string[], current: string | null, step: 1 | -1): string | null {
  if (ids.length === 0) return null
  const index = current === null ? -1 : ids.indexOf(current)
  if (index === -1) return step === 1 ? ids[0] : ids[ids.length - 1]
  return ids[Math.min(ids.length - 1, Math.max(0, index + step))]
}

/**
 * J / K move the active row down / up (spec §5.1). Listens on the document, so
 * it works with focus on a row, on the page or in the open panel, but never
 * while typing in a field or using a menu, nor with a modifier held (⌘K etc.).
 */
export function useRowShortcuts({
  ids,
  activeId,
  onMove,
  enabled = true,
}: {
  ids: readonly string[]
  activeId: string | null
  onMove: (applicationId: string) => void
  enabled?: boolean
}) {
  // The listener is registered once; it reads the latest values from a ref.
  const latest = useRef({ ids, activeId, onMove })
  useEffect(() => {
    latest.current = { ids, activeId, onMove }
  })

  useEffect(() => {
    if (!enabled) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return
      if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return
      const key = event.key.toLowerCase()
      if (key !== "j" && key !== "k") return
      if (isTypingTarget(event.target)) return
      const { ids: current, activeId: active, onMove: move } = latest.current
      const next = adjacentId(current, active, key === "j" ? 1 : -1)
      if (next === null) return
      event.preventDefault()
      if (next !== active) move(next)
    }
    document.addEventListener("keydown", onKeyDown)
    return () => document.removeEventListener("keydown", onKeyDown)
  }, [enabled])
}
