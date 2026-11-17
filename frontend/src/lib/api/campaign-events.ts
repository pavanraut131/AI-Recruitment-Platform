import { API_V2_BASE_URL, type Schema } from "./client"
import type { paths } from "./schema"

/**
 * The campaign event stream, `GET /campaigns/{campaign_id}/events` (spec §6.1):
 * Server-Sent Events, first `connected`, then one `resume_update` each time
 * one of the campaign's resumes changes status. The payload types come from
 * the generated schema (`ConnectedEvent`, `ResumeEvent`), which the backend
 * lists in its document for this client.
 */

export type ConnectedEvent = Schema<"ConnectedEvent">
export type ResumeEvent = Schema<"ResumeEvent">
export type ResumeStatus = Schema<"ResumeStatus">

/** SSE event names (`event:` field) the stream sends. */
export const CAMPAIGN_EVENT = {
  connected: "connected",
  resumeUpdate: "resume_update",
} as const

const EVENTS_PATH = "/campaigns/{campaign_id}/events" satisfies keyof paths

export function campaignEventsUrl(campaignId: string, baseUrl: string = API_V2_BASE_URL): string {
  return `${baseUrl}${EVENTS_PATH.replace("{campaign_id}", encodeURIComponent(campaignId))}`
}

/** Every resume status. The type check below fails if the API adds one this list lacks. */
export const RESUME_STATUSES = [
  "pending",
  "extracting",
  "processing",
  "done",
  "failed",
  "quota_exceeded",
] as const satisfies readonly ResumeStatus[]

type MissingStatus = Exclude<ResumeStatus, (typeof RESUME_STATUSES)[number]>
const allStatusesListed: [MissingStatus] extends [never] ? true : MissingStatus = true
void allStatusesListed

/** Statuses after which a resume stops changing until it is retried. */
export const TERMINAL_RESUME_STATUSES: ReadonlySet<ResumeStatus> = new Set([
  "done",
  "failed",
  "quota_exceeded",
])

function isResumeStatus(value: unknown): value is ResumeStatus {
  return (RESUME_STATUSES as readonly unknown[]).includes(value)
}

const NULLABLE_STRING_FIELDS = ["filename", "error", "candidate_name", "category", "person_id"] as const

/**
 * A `resume_update` payload, or `null` when the data is not one (bad JSON, a
 * missing id, an unknown status). The backend validates every payload against
 * `ResumeEvent` before sending, so `null` means a contract mismatch; the hook
 * skips it rather than corrupting the cache. Absent optional fields become `null`.
 */
export function parseResumeEvent(data: string): ResumeEvent | null {
  let value: unknown
  try {
    value = JSON.parse(data)
  } catch {
    return null
  }
  if (typeof value !== "object" || value === null) return null
  const raw = value as Record<string, unknown>
  if (typeof raw.resume_id !== "string" || !isResumeStatus(raw.status)) return null
  for (const field of NULLABLE_STRING_FIELDS) {
    if (raw[field] != null && typeof raw[field] !== "string") return null
  }
  if (raw.score != null && typeof raw.score !== "number") return null
  if (raw.matched_existing != null && typeof raw.matched_existing !== "boolean") return null
  return {
    resume_id: raw.resume_id,
    status: raw.status,
    filename: (raw.filename as string | null | undefined) ?? null,
    error: (raw.error as string | null | undefined) ?? null,
    candidate_name: (raw.candidate_name as string | null | undefined) ?? null,
    score: (raw.score as number | null | undefined) ?? null,
    category: (raw.category as ResumeEvent["category"] | undefined) ?? null,
    person_id: (raw.person_id as string | null | undefined) ?? null,
    matched_existing: (raw.matched_existing as boolean | null | undefined) ?? null,
  }
}
