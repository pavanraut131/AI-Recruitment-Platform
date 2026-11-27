"use client"

import { AlertTriangleIcon, FilterXIcon, UploadIcon, UsersIcon, type LucideIcon } from "lucide-react"
import Link from "next/link"
import type { ReactNode } from "react"

import { Button } from "@/components/ui/button"
import { errorMessage } from "@/lib/api/problem"

function StateBlock({
  icon: Icon,
  title,
  description,
  action,
  role,
}: {
  icon: LucideIcon
  title: string
  description?: string
  action?: ReactNode
  role?: "alert" | "status"
}) {
  return (
    <div role={role} className="flex flex-col items-center gap-3 border-t px-6 py-16 text-center">
      <div className="flex size-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
        <Icon className="size-5" aria-hidden="true" />
      </div>
      <div className="flex flex-col gap-1">
        <p className="text-section font-semibold">{title}</p>
        {description && <p className="max-w-sm text-ink-2">{description}</p>}
      </div>
      {action && <div className="mt-1">{action}</div>}
    </div>
  )
}

/** The campaign has no applications yet. `uploadHref`: absent for a role that cannot upload. */
export function NoCandidates({ uploadHref }: { uploadHref?: string }) {
  return (
    <StateBlock
      icon={UsersIcon}
      title="No candidates yet"
      description={
        uploadHref
          ? "Upload their resumes. Each one is read, scored against this campaign's must-haves and ranked here."
          : "Once resumes are uploaded, each one is read, scored against this campaign's must-haves and ranked here."
      }
      action={
        uploadHref && (
          <Button asChild>
            <Link href={uploadHref}>
              <UploadIcon aria-hidden="true" />
              Upload resumes
            </Link>
          </Button>
        )
      }
    />
  )
}

/** The filters match nothing. */
export function NoMatches({ onClear }: { onClear: () => void }) {
  return (
    <StateBlock
      icon={FilterXIcon}
      title="No candidates match these filters"
      description="Try a wider score range, or fewer matches, stages or skills."
      action={
        <Button variant="outline" onClick={onClear}>
          Clear filters
        </Button>
      }
    />
  )
}

/** A page past the end (e.g. after rejecting the last rows of the last page). */
export function EmptyPage({ onFirstPage }: { onFirstPage: () => void }) {
  return (
    <StateBlock
      icon={UsersIcon}
      title="This page is empty"
      action={
        <Button variant="outline" onClick={onFirstPage}>
          Go to the first page
        </Button>
      }
    />
  )
}

/** The first load failed: the problem's title and detail, and a retry (spec §8). */
export function LoadError({ error, onRetry, retrying }: { error: unknown; onRetry: () => void; retrying: boolean }) {
  const { title, description } = errorMessage(error)
  return (
    <StateBlock
      role="alert"
      icon={AlertTriangleIcon}
      title={`Could not load candidates: ${title}`}
      description={description}
      action={
        <Button variant="outline" onClick={onRetry} disabled={retrying}>
          {retrying ? "Retrying…" : "Retry"}
        </Button>
      }
    />
  )
}
