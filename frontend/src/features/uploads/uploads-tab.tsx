"use client"

import { AlertTriangleIcon, ChevronLeftIcon, ChevronRightIcon, InboxIcon, RotateCwIcon } from "lucide-react"
import type { UseMutationResult, UseQueryResult } from "@tanstack/react-query"
import { useCallback, useEffect, useMemo, useState } from "react"

import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { errorMessage, type Schema } from "@/lib/api/client"
import { cn } from "@/lib/utils"

import { ResumeDropzone } from "./dropzone"
import { countSteps, pendingRows, uploadAnnouncement, type ClientRejection } from "./model"
import {
  RESUME_PAGE_SIZE,
  type ResumePage,
  type RetryResult,
  useCampaignResumes,
  useForgetBatches,
  useResumeCounts,
  useRetryFailed,
  useUploadBatches,
  useUploadResumes,
} from "./queries"
import { PendingRowItem, ResumeRowItem } from "./upload-rows"
import { useUploadProgress } from "./upload-progress"
import { toBatches } from "./validate"

let batchSequence = 0
function nextBatchId(): string {
  batchSequence += 1
  return `batch-${batchSequence}`
}

/** The current time, refreshed every minute (relative times, the quota reset). */
function useNow(intervalMs = 60_000): Date {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), intervalMs)
    return () => clearInterval(timer)
  }, [intervalMs])
  return now
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`
}

/**
 * The Uploads tab of a campaign workspace (spec §5.3): a dropzone for PDFs and
 * every file's progress, Queued → Uploading → Reading → Scoring → Done /
 * Failed / Quota limit.
 *
 * - Files this session has not turned into resumes yet (queued, uploading,
 *   rejected by the client check or the server, a failed request) are listed
 *   first, from the upload mutations (`queries.ts`).
 * - Below, the campaign's resumes, newest first. Their status is kept live by
 *   the workspace's `useCampaignEvents`, which patches this list in the Query
 *   cache: no polling here.
 */
export function UploadsTab({ campaignId }: { campaignId: string }) {
  const [page, setPage] = useState(1)
  const resumes = useCampaignResumes(campaignId, page)
  const counts = useResumeCounts(campaignId).data
  const upload = useUploadResumes(campaignId)
  const batches = useUploadBatches(campaignId)
  const progress = useUploadProgress()
  const forgetBatches = useForgetBatches(campaignId)
  const retry = useRetryFailed(campaignId)
  const now = useNow()

  const [clientRejections, setClientRejections] = useState<ClientRejection[]>([])
  const [dropMessage, setDropMessage] = useState("")

  const { mutate } = upload
  const onFiles = useCallback(
    (accepted: File[], rejected: { file: File; reason: string }[]) => {
      if (rejected.length > 0) {
        const stamp = nextBatchId()
        setClientRejections((previous) => [
          ...rejected.map(({ file, reason }, index) => ({
            key: `${stamp}:client:${index}`,
            filename: file.name,
            size: file.size,
            reason,
          })),
          ...previous,
        ])
      }
      for (const files of toBatches(accepted)) mutate({ batchId: nextBatchId(), files })
      const parts = []
      if (accepted.length > 0) parts.push(`Uploading ${plural(accepted.length, "file")}.`)
      if (rejected.length > 0) {
        parts.push(
          `${plural(rejected.length, "file")} not uploaded: ${rejected.map(({ file, reason }) => `${file.name} (${reason})`).join(", ")}.`,
        )
      }
      setDropMessage(parts.join(" "))
    },
    [mutate],
  )

  const onRetryBatch = useCallback(
    (mutationId: number) => {
      const batch = batches.find((candidate) => candidate.mutationId === mutationId)
      if (!batch) return
      forgetBatches([mutationId])
      mutate({ batchId: nextBatchId(), files: batch.files })
    },
    [batches, forgetBatches, mutate],
  )

  const pending = useMemo(() => pendingRows(batches, progress, clientRejections), [batches, progress, clientRejections])
  const finishedPending = pending.some((row) => row.step === "rejected" || row.step === "upload_failed")
  const clearFinished = () => {
    forgetBatches()
    setClientRejections([])
  }

  return (
    <div className="flex flex-col gap-6">
      <ResumeDropzone onFiles={onFiles} />

      {/* Live regions (spec §8): what was added, then the upload's progress. */}
      <p role="status" className="sr-only">
        {dropMessage}
      </p>
      <p role="status" className="sr-only">
        {uploadAnnouncement(batches, progress)}
      </p>

      {pending.length > 0 && (
        <section aria-labelledby="uploads-this-session" className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-3">
            <h2 id="uploads-this-session" className="text-section font-semibold">
              This upload
            </h2>
            {finishedPending && (
              <Button variant="ghost" size="sm" onClick={clearFinished}>
                Clear
              </Button>
            )}
          </div>
          <ul className="border-t" aria-label="Files of this upload">
            {pending.map((row) => (
              <PendingRowItem key={row.key} row={row} onRetryBatch={onRetryBatch} />
            ))}
          </ul>
        </section>
      )}

      <ResumesSection
        campaignId={campaignId}
        resumes={resumes}
        counts={counts}
        page={page}
        onPage={setPage}
        retry={retry}
        hasPending={pending.length > 0}
        now={now}
      />
    </div>
  )
}

function ResumesSection({
  campaignId,
  resumes,
  counts,
  page,
  onPage,
  retry,
  hasPending,
  now,
}: {
  campaignId: string
  resumes: UseQueryResult<ResumePage>
  counts: Schema<"Campaign">["counts"] | undefined
  page: number
  onPage: (page: number) => void
  retry: UseMutationResult<RetryResult, Error, void>
  hasPending: boolean
  now: Date
}) {
  const items = resumes.data?.items ?? []
  const total = resumes.data?.total ?? 0
  const onPageCounts = countSteps(items)
  // The campaign's count covers every page; the page's own count is fresher
  // for the rows on screen. Show whichever is larger.
  const failed = Math.max(counts?.failed ?? 0, onPageCounts.failed + onPageCounts.quota)
  const pages = Math.max(1, Math.ceil(total / RESUME_PAGE_SIZE))

  return (
    <section aria-labelledby="uploads-resumes" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="uploads-resumes" className="text-section font-semibold">
          Resumes
        </h2>
        {counts && counts.resumes > 0 && (
          <p className="text-dense text-muted-foreground">
            {plural(counts.resumes, "resume")}
            {counts.in_progress > 0 && ` · ${counts.in_progress} processing`}
            {counts.failed > 0 && ` · ${counts.failed} failed or quota-limited`}
          </p>
        )}
      </div>

      {failed > 0 && (
        <div
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-destructive/10 px-4 py-3"
          data-testid="partial-state"
        >
          <p className="flex items-start gap-2 text-sm">
            <AlertTriangleIcon className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden="true" />
            <span>
              {plural(failed, "resume")} could not be processed. Retrying queues {failed === 1 ? "it" : "them"} again
              from the step {failed === 1 ? "it" : "they"} stopped at.
            </span>
          </p>
          <Button variant="outline" size="sm" onClick={() => retry.mutate()} disabled={retry.isPending}>
            <RotateCwIcon
              data-icon="inline-start"
              aria-hidden="true"
              className={cn(retry.isPending && "animate-spin motion-reduce:animate-none")}
            />
            {retry.isPending ? "Retrying…" : "Retry failed"}
          </Button>
        </div>
      )}

      {resumes.isPending ? (
        <ul className="border-t" aria-busy="true" aria-label="Loading resumes">
          {Array.from({ length: 3 }, (_, index) => (
            <li key={index} className="flex items-center gap-3 border-b px-1 py-3">
              <Skeleton className="size-4" />
              <div className="flex flex-1 flex-col gap-1.5">
                <Skeleton className="h-4 w-56" />
                <Skeleton className="h-3 w-32" />
              </div>
              <Skeleton className="h-3.5 w-16" />
            </li>
          ))}
        </ul>
      ) : resumes.isError && !resumes.data ? (
        <LoadError error={resumes.error} onRetry={() => void resumes.refetch()} retrying={resumes.isFetching} />
      ) : total === 0 ? (
        hasPending ? null : (
          <div className="flex flex-col items-center gap-2 border-t px-6 py-10 text-center">
            <InboxIcon className="size-5 text-muted-foreground" aria-hidden="true" />
            <p className="text-section font-semibold">No resumes yet</p>
            <p className="max-w-sm text-ink-2">
              Drop PDFs above. Each one is read and scored, then shows up under Candidates.
            </p>
          </div>
        )
      ) : (
        <>
          <ul className="border-t" aria-label="Resumes of this campaign">
            {items.map((resume) => (
              <ResumeRowItem key={resume.id} resume={resume} campaignId={campaignId} now={now} />
            ))}
          </ul>
          {pages > 1 && (
            <nav aria-label="Resume pages" className="flex items-center justify-end gap-2 text-dense">
              <span className="text-muted-foreground">
                Page {page} of {pages}
              </span>
              <Button
                variant="outline"
                size="icon-sm"
                onClick={() => onPage(page - 1)}
                disabled={page <= 1}
                aria-label="Previous page"
              >
                <ChevronLeftIcon aria-hidden="true" />
              </Button>
              <Button
                variant="outline"
                size="icon-sm"
                onClick={() => onPage(page + 1)}
                disabled={page >= pages}
                aria-label="Next page"
              >
                <ChevronRightIcon aria-hidden="true" />
              </Button>
            </nav>
          )}
        </>
      )}
    </section>
  )
}

function LoadError({ error, onRetry, retrying }: { error: unknown; onRetry: () => void; retrying: boolean }) {
  const { title, description } = errorMessage(error)
  return (
    <div
      role="alert"
      className="flex flex-col items-center gap-3 border-t px-6 py-10 text-center"
    >
      <AlertTriangleIcon className="size-5 text-destructive" aria-hidden="true" />
      <div className="flex max-w-md flex-col gap-1">
        <p className="text-section font-semibold">Could not load the resumes: {title}</p>
        {description && <p className="text-ink-2">{description}</p>}
      </div>
      <Button variant="outline" onClick={onRetry} disabled={retrying}>
        {retrying ? "Retrying…" : "Try again"}
      </Button>
    </div>
  )
}
