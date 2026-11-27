/**
 * How long to wait, from a 429's `Retry-After` (seconds), in words:
 * "30 seconds", "1 minute", "15 minutes", "2 hours". Rounded up, so the
 * wait is never shorter than the API's.
 */
export function waitInWords(seconds: number): string {
  if (seconds < 60) {
    const s = Math.max(1, Math.ceil(seconds))
    return `${s} ${s === 1 ? "second" : "seconds"}`
  }
  const minutes = Math.ceil(seconds / 60)
  if (minutes < 60) return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`
  const hours = Math.ceil(minutes / 60)
  return `${hours} ${hours === 1 ? "hour" : "hours"}`
}
