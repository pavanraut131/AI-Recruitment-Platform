import { useSyncExternalStore } from "react"

/*
 * Bytes-sent progress of the upload requests in flight, by batch id.
 *
 * An upload batch is a TanStack Query mutation (queries.ts), so its status,
 * files and result live in the mutation cache and survive the Uploads tab
 * being unmounted (switching tabs does not stop or forget an upload). A
 * mutation's state has no progress, though, so the XHR's progress events are
 * kept here, next to it, for as long as the request runs; the entry is removed
 * when it settles. This is transient request state, not app state: no server
 * data, nothing persisted (ADR 0007 keeps server data out of stores).
 */

type Listener = () => void

const EMPTY: ReadonlyMap<string, number> = new Map()
let progress: ReadonlyMap<string, number> = EMPTY
const listeners = new Set<Listener>()

function emit(): void {
  for (const listener of listeners) listener()
}

export const uploadProgress = {
  set(batchId: string, fraction: number): void {
    if (progress.get(batchId) === fraction) return
    progress = new Map(progress).set(batchId, fraction)
    emit()
  },
  delete(batchId: string): void {
    if (!progress.has(batchId)) return
    const next = new Map(progress)
    next.delete(batchId)
    progress = next
    emit()
  },
  subscribe(listener: Listener): () => void {
    listeners.add(listener)
    return () => listeners.delete(listener)
  },
  snapshot(): ReadonlyMap<string, number> {
    return progress
  },
}

/** The fraction sent (0–1) of each batch being uploaded. */
export function useUploadProgress(): ReadonlyMap<string, number> {
  return useSyncExternalStore(uploadProgress.subscribe, uploadProgress.snapshot, () => EMPTY)
}
