import { useEffect, type RefObject } from "react"

/**
 * Page-level keyboard shortcuts (the candidates table's J/K, the review
 * panel's and Person profile's S) share these checks, so a letter typed into a
 * field or a menu never triggers one.
 */

/**
 * Whether a key press belongs to whatever has focus rather than to the page's
 * shortcuts: typing in a field, or moving inside a menu, listbox or popover.
 */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false
  if (target.closest("input, textarea, select, [contenteditable=''], [contenteditable='true']")) return true
  return target.closest("[role='menu'], [role='listbox'], [role='combobox'], [data-slot='popover-content']") !== null
}

/** Whether a modifier is held (⌘K and friends are not single-letter shortcuts). */
export function hasModifier(event: KeyboardEvent): boolean {
  return event.metaKey || event.ctrlKey || event.altKey || event.shiftKey
}

/**
 * Whether `event` is the single-letter shortcut `key` (case-insensitive) and
 * nothing else claims it: not handled yet, no modifier, not typed into a field.
 */
export function isLetterShortcut(event: KeyboardEvent, key: string): boolean {
  if (event.defaultPrevented || hasModifier(event)) return false
  if (event.key.toLowerCase() !== key) return false
  return !isTypingTarget(event.target)
}

/**
 * A single-letter shortcut that moves focus to `target` (S focuses the stage
 * selector in the review panel and on the Person profile). Listens on the
 * document while `enabled`.
 */
export function useFocusShortcut(key: string, target: RefObject<HTMLElement | null>, enabled = true): void {
  useEffect(() => {
    if (!enabled) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (!isLetterShortcut(event, key) || !target.current) return
      event.preventDefault()
      target.current.focus()
    }
    document.addEventListener("keydown", onKeyDown)
    return () => document.removeEventListener("keydown", onKeyDown)
  }, [key, target, enabled])
}
