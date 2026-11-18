"use client"

import { useCallback, useEffect, useRef, useState } from "react"

/**
 * Debounced autosave for a text field (the notes of an application, in the
 * review panel and on the Person profile).
 *
 * - Typing changes the local draft only: the field re-renders, nothing else
 *   does. After `delayMs` without typing the draft is saved; one save runs at a
 *   time, and text typed during a save is saved after it (never out of order).
 * - `status` is what the UI shows: `pending` (typed, waiting for the pause) and
 *   `saving` read "Saving…", then `saved` or `error`.
 * - A failed save keeps the draft (and the error) until the next attempt:
 *   typing again or `flush()` (a Retry button, leaving the field).
 * - Unmounting (closing the panel, switching application) flushes a pending
 *   draft. If that last save fails, `onUnmountedError` is called (the field is
 *   gone, so a toast tells) and the text is kept in memory under `key`: the
 *   next editor mounted with that key starts from it and saves it again.
 * - A new server `value` replaces the draft only when there is nothing local
 *   to keep (no unsaved text, no save running).
 *
 * The hook assumes one `key` per mounted editor: key the component by it.
 */

export type AutosaveStatus = "idle" | "pending" | "saving" | "saved" | "error"

/** The pause after the last key press before saving. */
export const AUTOSAVE_DELAY_MS = 800

/** Text that has not reached the server yet, by key (survives the editor unmounting). */
const unsavedDrafts = new Map<string, string>()

/** Forget every kept draft (tests). */
export function clearUnsavedDrafts(): void {
  unsavedDrafts.clear()
}

type Machine = {
  key: string
  /** The latest text typed. */
  latest: string
  /** The latest text the server has. */
  saved: string
  timer: ReturnType<typeof setTimeout> | undefined
  inFlight: boolean
  /** Text changed while a save was running: save again when it ends. */
  queued: boolean
  mounted: boolean
  save: (value: string) => Promise<void>
  onUnmountedError?: (error: unknown) => void
  delayMs: number
  /** The hook's state setters (stable), for the save loop below. */
  ui: {
    setSaved: (value: string) => void
    setStatus: (update: AutosaveStatus | ((current: AutosaveStatus) => AutosaveStatus)) => void
    setError: (error: unknown) => void
  }
}

/** Save the latest text if it differs from the server's; one save at a time. */
async function runSave(m: Machine): Promise<void> {
  clearTimeout(m.timer)
  m.timer = undefined
  if (m.inFlight) {
    m.queued = true
    return
  }
  const text = m.latest
  if (text === m.saved) {
    unsavedDrafts.delete(m.key)
    if (m.mounted) m.ui.setStatus((current) => (current === "pending" || current === "error" ? "idle" : current))
    return
  }
  m.inFlight = true
  m.queued = false
  if (m.mounted) m.ui.setStatus("saving")
  try {
    await m.save(text)
    m.saved = text
    if (unsavedDrafts.get(m.key) === text) unsavedDrafts.delete(m.key)
    if (m.mounted) {
      m.ui.setSaved(text)
      m.ui.setError(null)
      m.ui.setStatus(m.latest === text ? "saved" : "pending")
    }
  } catch (failure) {
    if (m.mounted) {
      m.ui.setError(failure)
      m.ui.setStatus("error")
    } else {
      m.onUnmountedError?.(failure)
    }
  } finally {
    m.inFlight = false
  }
  // Text typed during the save (or the editor closed with newer text): save that too.
  if (m.queued) {
    m.queued = false
    if (m.latest !== text) await runSave(m)
  }
}

export function useAutosave({
  key,
  value,
  save,
  delayMs = AUTOSAVE_DELAY_MS,
  onUnmountedError,
}: {
  /** Identifies the saved thing (e.g. `application:<id>:notes`). */
  key: string
  /** The server's current value. */
  value: string
  /** Persist a value; rejects on failure. */
  save: (value: string) => Promise<void>
  delayMs?: number
  /** A save started while unmounting failed. */
  onUnmountedError?: (error: unknown) => void
}) {
  const [kept] = useState(() => unsavedDrafts.get(key))
  const [draft, setDraft] = useState(kept ?? value)
  const [saved, setSaved] = useState(value)
  const [status, setStatus] = useState<AutosaveStatus>(kept !== undefined && kept !== value ? "pending" : "idle")
  const [error, setError] = useState<unknown>(null)

  // A newer server value (another tab, a refetch) replaces an untouched draft.
  const [server, setServer] = useState(value)
  if (value !== server) {
    setServer(value)
    if (draft === saved && (status === "idle" || status === "saved")) {
      setDraft(value)
      setSaved(value)
    }
  }

  const machine = useRef<Machine>({
    key,
    latest: draft,
    saved: value,
    timer: undefined,
    inFlight: false,
    queued: false,
    mounted: false,
    save,
    onUnmountedError,
    delayMs,
    ui: { setSaved, setStatus, setError },
  })
  useEffect(() => {
    Object.assign(machine.current, { key, save, onUnmountedError, delayMs })
  })
  useEffect(() => {
    // Adopted server values (above) reach the machine here.
    machine.current.latest = draft
    machine.current.saved = saved
  }, [draft, saved])

  const run = useCallback(() => runSave(machine.current), [])

  const onChange = useCallback(
    (text: string) => {
      const m = machine.current
      m.latest = text
      setDraft(text)
      clearTimeout(m.timer)
      m.timer = undefined
      if (text === m.saved && !m.inFlight) {
        unsavedDrafts.delete(m.key)
        setStatus("idle")
        setError(null)
        return
      }
      unsavedDrafts.set(m.key, text)
      if (!m.inFlight) setStatus("pending")
      m.timer = setTimeout(() => void run(), m.delayMs)
    },
    [run],
  )

  /** Save now if there is anything to save (Retry, leaving the field). */
  const flush = useCallback(() => run(), [run])

  useEffect(() => {
    const m = machine.current
    m.mounted = true
    // Text kept from an editor that closed with a failed save: try again.
    if (m.latest !== m.saved) m.timer = setTimeout(() => void run(), 0)
    return () => {
      m.mounted = false
      clearTimeout(m.timer)
      m.timer = undefined
      if (m.latest === m.saved) return
      if (m.inFlight) m.queued = true
      else void run()
    }
  }, [run])

  return { draft, onChange, flush, status, error }
}
