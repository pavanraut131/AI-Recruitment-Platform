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
    <div
      role={role}
      className="flex flex-col items-center gap-3 rounded-lg border border-dashed px-6 py-14 text-center"
    >
      <div className="flex size-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
        <Icon className="size-5" aria-hidden="true" />
      </div>
      <div className="flex flex-col gap-1">
        <p className="font-medium">{title}</p>
        {description && <p className="max-w-sm text-sm text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  )
}

/** The campaign has no applications yet. */
export function NoCandidates({ uploadHref }: { uploadHref: string }) {
  return (
    <StateBlock
      icon={UsersIcon}
      title="No candidates yet"
      description="Upload resumes and HireFlow AI will extract, score and rank each candidate here."
      action={
        <Button asChild size="sm">
          <Link href={uploadHref}>
            <UploadIcon aria-hidden="true" />
            Upload resumes
          </Link>
        </Button>
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
      description="Try a wider score range or fewer categories, stages or skills."
      action={
        <Button variant="outline" size="sm" onClick={onClear}>
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
        <Button variant="outline" size="sm" onClick={onFirstPage}>
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
        <Button variant="outline" size="sm" onClick={onRetry} disabled={retrying}>
          {retrying ? "Retrying…" : "Retry"}
        </Button>
      }
    />
  )
}
