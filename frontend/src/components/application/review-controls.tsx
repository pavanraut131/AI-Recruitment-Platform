"use client"

import { useQueryClient } from "@tanstack/react-query"
import { CheckIcon, CircleAlertIcon, LoaderCircleIcon } from "lucide-react"
import { useCallback, useId, type ReactNode, type Ref } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { errorMessage } from "@/lib/api/client"
import type { Stage } from "@/lib/applications"
import { saveApplicationNotes, useUpdateApplicationStage, type ApplicationRef } from "@/lib/query/applications"
import { useAutosave, type AutosaveStatus } from "@/lib/use-autosave"
import { cn } from "@/lib/utils"

import { StageSelect } from "./stage-select"

/*
 * The recruiter's controls on one application, shared by the review panel and
 * the Person profile: the stage (saved at once, optimistically) and the notes
 * (autosaved). Both keep every cached copy of the application in step
 * (`@/lib/query/applications`).
 */

/** The stage selector, saving each change with `updateApplication`. */
export function ApplicationStageSelect({
  target,
  stage,
  triggerRef,
  id,
  className,
  "aria-describedby": ariaDescribedBy,
}: {
  target: ApplicationRef
  stage: Stage | null
  triggerRef?: Ref<HTMLButtonElement>
  id?: string
  className?: string
  "aria-describedby"?: string
}) {
  const update = useUpdateApplicationStage()
  return (
    <StageSelect
      ref={triggerRef}
      id={id}
      className={className}
      aria-describedby={ariaDescribedBy}
      value={stage}
      onValueChange={(next) => {
        if (next !== stage) update.mutate({ ...target, stage: next })
      }}
    />
  )
}

function NotesStatus({
  id,
  status,
  error,
  onRetry,
}: {
  id: string
  status: AutosaveStatus
  error: unknown
  onRetry: () => void
}) {
  let content: ReactNode
  switch (status) {
    case "pending":
    case "saving":
      content = (
        <>
          <LoaderCircleIcon className="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          Saving…
        </>
      )
      break
    case "saved":
      content = (
        <>
          <CheckIcon className="size-3.5 text-category-strong" aria-hidden="true" />
          Saved
        </>
      )
      break
    case "error": {
      const { title, description } = errorMessage(error)
      content = (
        <>
          <CircleAlertIcon className="size-3.5 shrink-0" aria-hidden="true" />
          <span className="truncate" title={description}>
            Not saved: {description ?? title}
          </span>
        </>
      )
      break
    }
    case "idle":
      content = "Saved automatically as you type"
      break
  }
  return (
    <div className="flex min-h-7 items-center justify-between gap-2 text-xs">
      <p
        id={id}
        role="status"
        data-status={status}
        className={cn(
          "flex min-w-0 items-center gap-1.5 text-muted-foreground",
          status === "error" && "text-destructive",
        )}
      >
        {content}
      </p>
      {status === "error" && (
        <Button type="button" variant="outline" size="xs" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  )
}

/**
 * The application's notes, autosaved after a pause in typing (`useAutosave`):
 * "Saving…", "Saved", or the problem with a Retry. Leaving the field saves at
 * once; unmounting (closing the panel, switching application) saves what is
 * pending. Key it by the application id.
 */
export function ApplicationNotes({
  target,
  notes,
  label = "Notes",
  className,
}: {
  target: ApplicationRef
  notes: string | null
  label?: string
  className?: string
}) {
  const queryClient = useQueryClient()
  const fieldId = useId()
  const statusId = useId()
  const { applicationId, campaignId, personId } = target
  const save = useCallback(
    (text: string) => saveApplicationNotes(queryClient, { applicationId, campaignId, personId }, text),
    [queryClient, applicationId, campaignId, personId],
  )
  const onUnmountedError = useCallback((error: unknown) => {
    const { title, description } = errorMessage(error)
    toast.error("Notes not saved", {
      description: `${description ?? title}. Your text is kept: open the candidate again to retry.`,
    })
  }, [])
  const { draft, onChange, flush, status, error } = useAutosave({
    key: `application:${applicationId}:notes`,
    value: notes ?? "",
    save,
    onUnmountedError,
  })

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <label htmlFor={fieldId} className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
        {label}
      </label>
      <Textarea
        id={fieldId}
        value={draft}
        onChange={(event) => onChange(event.target.value)}
        onBlur={() => void flush()}
        aria-describedby={statusId}
        aria-invalid={status === "error" || undefined}
        placeholder="Add notes about this candidate: calls, impressions, next steps…"
        className="min-h-24 text-sm"
      />
      <NotesStatus id={statusId} status={status} error={error} onRetry={() => void flush()} />
    </div>
  )
}
