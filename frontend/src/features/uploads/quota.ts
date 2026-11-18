import { QUOTA_RESET_TIME_ZONE } from "./limits"

const HOUR_MS = 3_600_000
const DAY_MS = 24 * HOUR_MS

const wallClock = new Intl.DateTimeFormat("en-US", {
  timeZone: QUOTA_RESET_TIME_ZONE,
  hourCycle: "h23",
  hour: "numeric",
  minute: "numeric",
  second: "numeric",
})

/** Milliseconds since midnight in the quota's time zone at `instant`. */
function sinceMidnight(instant: Date): number {
  const parts = Object.fromEntries(wallClock.formatToParts(instant).map((part) => [part.type, part.value]))
  const hours = Number(parts.hour) % 24
  return ((hours * 60 + Number(parts.minute)) * 60 + Number(parts.second)) * 1000 + instant.getUTCMilliseconds()
}

/**
 * When the AI provider's daily quota next resets: the next midnight in
 * {@link QUOTA_RESET_TIME_ZONE} (Gemini's free tier; the backend says so in
 * its quota message). The API does not send a retry time, so this is the
 * provider's documented reset, not the backend's own schedule.
 */
export function nextQuotaReset(now: Date = new Date()): Date {
  let reset = now.getTime() - sinceMidnight(now) + DAY_MS
  // A day with a DST change is 23 or 25 hours long: land on midnight itself.
  const drift = sinceMidnight(new Date(reset))
  if (drift !== 0) reset += drift > DAY_MS / 2 ? DAY_MS - drift : -drift
  return new Date(reset)
}
