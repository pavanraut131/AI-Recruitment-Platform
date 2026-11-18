import type { Schema } from "@/lib/api/client"

import type { Resume, UploadBatch } from "./queries"
import { perFileProgress } from "./validate"

/**
 * The steps a file moves through on the Uploads tab (spec §5.3):
 * Queued → Uploading → Extracting → Analysing → Done, or Failed / Quota limit;
 * Rejected for a file that never became a resume (client check or server),
 * Upload failed for a request that got no answer or an error.
 */
export type UploadStep =
  | "queued"
  | "uploading"
  | "extracting"
  | "analysing"
  | "done"
  | "failed"
  | "quota"
  | "rejected"
  | "upload_failed"

export type StepTone = "progress" | "done" | "failed" | "warning" | "muted"

export const STEP_META: Record<UploadStep, { label: string; tone: StepTone }> = {
  queued: { label: "Queued", tone: "muted" },
  uploading: { label: "Uploading", tone: "progress" },
  extracting: { label: "Extracting", tone: "progress" },
  analysing: { label: "Analysing", tone: "progress" },
  done: { label: "Done", tone: "done" },
  failed: { label: "Failed", tone: "failed" },
  quota: { label: "Quota limit", tone: "warning" },
  rejected: { label: "Rejected", tone: "failed" },
  upload_failed: { label: "Upload failed", tone: "failed" },
}

/**
 * A resume's step, from its `status`:
 *
 * | status           | step        |
 * |------------------|-------------|
 * | `pending`        | Queued (stored, waiting for a worker) |
 * | `extracting`     | Extracting (PDF text) |
 * | `processing`     | Analysing (AI profile, person, score, save) |
 * | `done`           | Done        |
 * | `failed`         | Failed (at `processing_step`) |
 * | `quota_exceeded` | Quota limit |
 *
 * `processing_step` only names the failed step: events do not carry it, so
 * for a resume in progress the cached value may be stale.
 */
export const RESUME_STATUS_STEP: Record<Schema<"ResumeStatus">, UploadStep> = {
  pending: "queued",
  extracting: "extracting",
  processing: "analysing",
  done: "done",
  failed: "failed",
  quota_exceeded: "quota",
}

/** The pipeline step that failed, for people ("Failed while scoring"). */
export const PROCESSING_STEP_LABELS: Record<Schema<"ProcessingStep">, string> = {
  extract_text: "reading the PDF",
  extract_profile: "extracting the profile",
  resolve_person: "matching the person",
  score: "scoring",
  save: "saving",
}

/** A file of this session that is not (or not yet) a resume: queued, uploading or rejected. */
export type PendingRow = {
  key: string
  filename: string
  size: number | null
  step: "queued" | "uploading" | "rejected" | "upload_failed"
  /** Uploading: the fraction of this file sent (estimated). */
  progress?: number
  /** Rejected / upload failed: why. */
  reason?: string
  /** Upload failed: the batch, to send again. */
  mutationId?: number
}

/** A file the client refused before uploading. */
export type ClientRejection = { key: string; filename: string; size: number; reason: string }

/**
 * The rows for files that are not resumes in the list: rejected by the client
 * check, waiting or being sent, refused by the server, or in a failed request.
 * Accepted files are left out: they are resumes now, in the campaign's list.
 * Newest first, like the list.
 */
export function pendingRows(
  batches: readonly UploadBatch[],
  progress: ReadonlyMap<string, number>,
  clientRejections: readonly ClientRejection[],
): PendingRow[] {
  const rows: PendingRow[] = []
  for (const batch of [...batches].reverse()) {
    if (batch.phase === "done") {
      batch.result?.rejected.forEach(({ filename, reason }, index) =>
        rows.push({ key: `${batch.batchId}:rejected:${index}`, filename, size: sizeOf(batch, filename), step: "rejected", reason }),
      )
      continue
    }
    const fileProgress =
      batch.phase === "uploading" ? perFileProgress(batch.files.map((file) => file.size), progress.get(batch.batchId) ?? 0) : []
    batch.files.forEach((file, index) => {
      const key = `${batch.batchId}:${index}`
      if (batch.phase === "error") {
        rows.push({
          key,
          filename: file.name,
          size: file.size,
          step: "upload_failed",
          reason: batch.error?.message,
          mutationId: batch.mutationId,
        })
      } else if (batch.phase === "uploading") {
        rows.push({ key, filename: file.name, size: file.size, step: "uploading", progress: fileProgress[index] })
      } else {
        rows.push({ key, filename: file.name, size: file.size, step: "queued" })
      }
    })
  }
  for (const rejection of clientRejections) {
    rows.push({ key: rejection.key, filename: rejection.filename, size: rejection.size, step: "rejected", reason: rejection.reason })
  }
  return rows
}

function sizeOf(batch: UploadBatch, filename: string): number | null {
  return batch.files.find((file) => file.name === filename)?.size ?? null
}

/**
 * What the live region says about the uploads (spec §8): the batch being sent
 * (in 25 % steps, so a screen reader is not flooded) and how many wait, else
 * the outcome of the latest batch.
 */
export function uploadAnnouncement(batches: readonly UploadBatch[], progress: ReadonlyMap<string, number>): string {
  const queued = batches.filter((batch) => batch.phase === "queued")
  const queuedFiles = queued.reduce((sum, batch) => sum + batch.files.length, 0)
  const waiting = queuedFiles > 0 ? ` ${files(queuedFiles)} queued.` : ""
  const sending = batches.find((batch) => batch.phase === "uploading")
  if (sending) {
    const percent = Math.floor(((progress.get(sending.batchId) ?? 0) * 100) / 25) * 25
    return `Uploading ${files(sending.files.length)}: ${percent}%.${waiting}`
  }
  const last = batches.findLast((batch) => batch.phase === "done" || batch.phase === "error")
  if (!last) return waiting.trim()
  if (last.phase === "error") {
    return `Upload of ${files(last.files.length)} failed: ${last.error?.message ?? "unknown error"}.${waiting}`
  }
  const accepted = last.result?.accepted.length ?? 0
  const rejected = last.result?.rejected.length ?? 0
  const outcome = rejected > 0 ? `${accepted} accepted, ${rejected} rejected` : `all accepted`
  return `Uploaded ${files(last.files.length)}: ${outcome}.${waiting}`
}

function files(count: number): string {
  return `${count} ${count === 1 ? "file" : "files"}`
}

/** Counts of the resumes on screen, by step (for the partial state). */
export function countSteps(resumes: readonly Resume[]): Record<"inProgress" | "done" | "failed" | "quota", number> {
  const counts = { inProgress: 0, done: 0, failed: 0, quota: 0 }
  for (const resume of resumes) {
    const step = RESUME_STATUS_STEP[resume.status]
    if (step === "done") counts.done += 1
    else if (step === "failed") counts.failed += 1
    else if (step === "quota") counts.quota += 1
    else counts.inProgress += 1
  }
  return counts
}
