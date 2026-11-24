import { describe, expect, it } from "vitest"

import { RESUME_STATUSES } from "@/lib/api/campaign-events"

import { pendingRows, RESUME_STATUS_STEP, STEP_META, uploadAnnouncement } from "./model"
import type { UploadBatch } from "./queries"
import { makeFile, makeResume } from "./test-utils"

function batch(overrides: Partial<UploadBatch> & Pick<UploadBatch, "batchId" | "phase">): UploadBatch {
  return { mutationId: 1, files: [makeFile("a.pdf", 100), makeFile("b.pdf", 300)], result: undefined, error: null, ...overrides }
}

describe("RESUME_STATUS_STEP", () => {
  it("maps every resume status onto the spec's steps", () => {
    const labels = Object.fromEntries(RESUME_STATUSES.map((status) => [status, STEP_META[RESUME_STATUS_STEP[status]].label]))
    expect(labels).toEqual({
      pending: "Queued",
      extracting: "Reading",
      processing: "Scoring",
      done: "Done",
      failed: "Failed",
      quota_exceeded: "Quota limit",
    })
  })
})

describe("pendingRows", () => {
  it("lists queued and uploading files (with each file's share of the progress), newest batch first", () => {
    const rows = pendingRows(
      [batch({ batchId: "b1", phase: "uploading" }), batch({ batchId: "b2", phase: "queued", mutationId: 2 })],
      new Map([["b1", 0.5]]),
      [],
    )
    expect(rows.map(({ key, step, progress }) => ({ key, step, progress }))).toEqual([
      { key: "b2:0", step: "queued", progress: undefined },
      { key: "b2:1", step: "queued", progress: undefined },
      { key: "b1:0", step: "uploading", progress: 1 },
      { key: "b1:1", step: "uploading", progress: 1 / 3 },
    ])
  })

  it("keeps only the rejected files of a finished batch (the accepted ones are resumes now)", () => {
    const rows = pendingRows(
      [
        batch({
          batchId: "b1",
          phase: "done",
          result: {
            accepted: [makeResume("r-1", { filename: "a.pdf" })],
            rejected: [{ filename: "b.pdf", reason: "Duplicate file (currently in progress)" }],
          },
        }),
      ],
      new Map(),
      [{ key: "c1", filename: "notes.docx", size: 10, reason: "Only PDF files are accepted" }],
    )
    expect(rows).toEqual([
      { key: "b1:rejected:0", filename: "b.pdf", size: 300, step: "rejected", reason: "Duplicate file (currently in progress)" },
      { key: "c1", filename: "notes.docx", size: 10, step: "rejected", reason: "Only PDF files are accepted" },
    ])
  })

  it("marks every file of a failed request, with the batch to send again", () => {
    const rows = pendingRows(
      [batch({ batchId: "b1", phase: "error", mutationId: 7, error: new Error("Could not reach the server") })],
      new Map(),
      [],
    )
    expect(rows.map(({ step, reason, mutationId }) => ({ step, reason, mutationId }))).toEqual([
      { step: "upload_failed", reason: "Could not reach the server", mutationId: 7 },
      { step: "upload_failed", reason: "Could not reach the server", mutationId: 7 },
    ])
  })
})

describe("uploadAnnouncement (the live region)", () => {
  it("says how far the current batch is, in 25 % steps, and what is queued", () => {
    const batches = [batch({ batchId: "b1", phase: "uploading" }), batch({ batchId: "b2", phase: "queued" })]
    expect(uploadAnnouncement(batches, new Map([["b1", 0.1]]))).toBe("Uploading 2 files: 0%. 2 files queued.")
    expect(uploadAnnouncement(batches, new Map([["b1", 0.6]]))).toBe("Uploading 2 files: 50%. 2 files queued.")
  })

  it("then the outcome of the latest batch", () => {
    const done = batch({
      batchId: "b1",
      phase: "done",
      result: { accepted: [makeResume("r-1")], rejected: [{ filename: "b.pdf", reason: "Invalid PDF file" }] },
    })
    expect(uploadAnnouncement([done], new Map())).toBe("Uploaded 2 files: 1 accepted, 1 rejected.")
    const failed = batch({ batchId: "b2", phase: "error", error: new Error("Network error") })
    expect(uploadAnnouncement([done, failed], new Map())).toBe("Upload of 2 files failed: Network error.")
    expect(uploadAnnouncement([], new Map())).toBe("")
  })
})
