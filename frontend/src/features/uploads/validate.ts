import { MAX_FILE_SIZE_BYTES, MAX_FILES_PER_BATCH, PDF_EXTENSION, REJECTION_REASONS } from "./limits"

/**
 * Why the server would reject this file before reading it, or `null`. The same
 * order and wording as the server (`_accept_uploads`): the name's extension
 * first, then the size. Content checks (`%PDF` magic bytes, duplicates) are
 * left to the server.
 */
export function checkFile(file: Pick<File, "name" | "size">): string | null {
  if (!file.name.toLowerCase().endsWith(PDF_EXTENSION)) return REJECTION_REASONS.type
  if (file.size > MAX_FILE_SIZE_BYTES) return REJECTION_REASONS.size
  return null
}

/** The files in request-sized batches (the server answers 422 above `MAX_FILES_PER_BATCH`). */
export function toBatches<T>(files: readonly T[], size: number = MAX_FILES_PER_BATCH): T[][] {
  const batches: T[][] = []
  for (let start = 0; start < files.length; start += size) batches.push(files.slice(start, start + size))
  return batches
}

/**
 * How far each file of a multipart body has been sent, from the request's
 * overall fraction: the parts are sent in order, so a file is done once the
 * bytes before it and its own have gone. Ignores the multipart overhead (a few
 * hundred bytes per part), so it is an estimate.
 */
export function perFileProgress(sizes: readonly number[], fraction: number): number[] {
  const total = sizes.reduce((sum, size) => sum + size, 0)
  if (total === 0) return sizes.map(() => fraction)
  const sent = Math.min(1, Math.max(0, fraction)) * total
  let offset = 0
  return sizes.map((size) => {
    const start = offset
    offset += size
    if (size === 0) return sent >= start ? 1 : 0
    return Math.min(1, Math.max(0, (sent - start) / size))
  })
}

const UNITS = ["KB", "MB", "GB"] as const

/** A file size for people: `840 B`, `312 KB`, `2.4 MB`. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${UNITS[unit]}`
}
