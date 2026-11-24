"use client"

import { formatDistanceStrict, formatDistanceToNowStrict } from "date-fns"
import {
  AlertTriangleIcon,
  ArrowRightIcon,
  CheckIcon,
  ClockIcon,
  FileTextIcon,
  LoaderCircleIcon,
  RotateCwIcon,
  XIcon,
  type LucideIcon,
} from "lucide-react"
import Link from "next/link"
import type { CSSProperties } from "react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

import { PROCESSING_STEP_LABELS, RESUME_STATUS_STEP, STEP_META, type PendingRow, type StepTone, type UploadStep } from "./model"
import type { Resume } from "./queries"
import { nextQuotaReset } from "./quota"
import { formatBytes } from "./validate"

/** A step as an icon and a word in its colour: no pill, the word carries it. */
const TONE: Record<StepTone, { className: string; icon: LucideIcon }> = {
  progress: { className: "text-ink-2", icon: LoaderCircleIcon },
  done: { className: "text-category-strong-foreground", icon: CheckIcon },
  failed: { className: "text-destructive", icon: XIcon },
  warning: { className: "text-category-moderate-foreground", icon: ClockIcon },
  muted: { className: "text-muted-foreground", icon: ClockIcon },
}

export function StepBadge({ step }: { step: UploadStep }) {
  const { label, tone } = STEP_META[step]
  const { className, icon: Icon } = TONE[tone]
  return (
    <span data-step={step} className={cn("inline-flex shrink-0 items-center gap-1.5 text-dense font-medium", className)}>
      <Icon
        aria-hidden="true"
        className={cn("size-3.5", tone === "progress" && "animate-spin text-primary motion-reduce:animate-none")}
      />
      {label}
    </span>
  )
}

function ProgressBar({ value, label }: { value: number; label: string }) {
  const percent = Math.round(Math.min(1, Math.max(0, value)) * 100)
  return (
    <div className="flex items-center gap-2">
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        className="h-1.5 w-40 overflow-hidden rounded-full bg-border"
      >
        <div
          className="h-full w-(--progress) rounded-full bg-primary transition-[width]"
          style={{ "--progress": `${percent}%` } as CSSProperties}
        />
      </div>
      <span className="text-xs text-muted-foreground">{percent}%</span>
    </div>
  )
}

function RowFrame({
  filename,
  meta,
  step,
  children,
}: {
  filename: string
  meta: React.ReactNode
  step: UploadStep
  children?: React.ReactNode
}) {
  return (
    <li className="flex items-start gap-3 border-b px-1 py-3" data-step={step}>
      <FileTextIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex min-w-0 items-center justify-between gap-3">
          <p className="truncate text-sm font-semibold" title={filename}>
            {filename}
          </p>
          <StepBadge step={step} />
        </div>
        <p className="text-xs text-muted-foreground">{meta}</p>
        {children}
      </div>
    </li>
  )
}

/** A file that is not a resume (yet): queued, uploading, rejected, or in a failed request. */
export function PendingRowItem({ row, onRetryBatch }: { row: PendingRow; onRetryBatch: (mutationId: number) => void }) {
  return (
    <RowFrame filename={row.filename} meta={row.size === null ? "This upload" : formatBytes(row.size)} step={row.step}>
      {row.step === "uploading" && <ProgressBar value={row.progress ?? 0} label={`Uploading ${row.filename}`} />}
      {row.step === "rejected" && <p className="text-sm text-destructive">{row.reason}</p>}
      {row.step === "upload_failed" && (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm text-destructive">{row.reason ?? "The upload failed."}</p>
          {row.mutationId !== undefined && (
            <Button variant="outline" size="xs" onClick={() => onRetryBatch(row.mutationId!)}>
              <RotateCwIcon data-icon="inline-start" aria-hidden="true" />
              Upload again
            </Button>
          )}
        </div>
      )}
    </RowFrame>
  )
}

/** A resume of the campaign, at its processing step (live through the cache). */
export function ResumeRowItem({ resume, campaignId, now }: { resume: Resume; campaignId: string; now: Date }) {
  const step = RESUME_STATUS_STEP[resume.status]
  const uploaded = new Date(resume.uploaded_at)
  const meta = (
    <>
      {formatBytes(resume.size_bytes)} · uploaded{" "}
      <time dateTime={resume.uploaded_at} title={uploaded.toLocaleString()}>
        {formatDistanceToNowStrict(uploaded, { addSuffix: true })}
      </time>
    </>
  )
  return (
    <RowFrame filename={resume.filename} meta={meta} step={step}>
      {step === "done" && <DoneDetail applicationId={resume.application_id} campaignId={campaignId} />}
      {step === "failed" && <FailedDetail resume={resume} />}
      {step === "quota" && <QuotaDetail now={now} />}
    </RowFrame>
  )
}

function DoneDetail({ applicationId, campaignId }: { applicationId: string | null; campaignId: string }) {
  if (applicationId === null) return <p className="text-sm text-muted-foreground">Scored. The candidate appears in a moment.</p>
  const query = new URLSearchParams({ tab: "candidates", open: applicationId })
  return (
    <Link
      href={`/campaigns/${encodeURIComponent(campaignId)}?${query}`}
      className="text-link inline-flex w-fit items-center gap-1 text-sm"
    >
      View candidate
      <ArrowRightIcon className="size-3.5" aria-hidden="true" />
    </Link>
  )
}

function FailedDetail({ resume }: { resume: Resume }) {
  const step = resume.processing_step ? PROCESSING_STEP_LABELS[resume.processing_step] : null
  return (
    <p className="text-sm">
      <span className="font-medium text-destructive">{step ? `Failed while ${step}` : "Failed"}</span>
      {resume.error_message && <span className="text-muted-foreground">: {resume.error_message}</span>}
    </p>
  )
}

function QuotaDetail({ now }: { now: Date }) {
  const reset = nextQuotaReset(now)
  const at = reset.toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })
  return (
    <p className="flex items-start gap-1.5 text-sm text-category-moderate-foreground">
      <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
      <span>
        The scoring service&apos;s daily quota is used up. Retry after it resets:{" "}
        <time dateTime={reset.toISOString()} className="font-medium">
          {at}
        </time>{" "}
        (in {formatDistanceStrict(reset, now)}).
      </span>
    </p>
  )
}
