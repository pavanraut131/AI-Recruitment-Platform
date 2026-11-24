import type { LucideIcon } from "lucide-react"
import { AlertCircleIcon, RotateCwIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { errorMessage } from "@/lib/api/client"
import { cn } from "@/lib/utils"

/*
 * Empty and error states for data views (spec §8: every view has designed
 * states). Shared by every feature's screens. No box around them: a quiet
 * icon, one line that says what happened, one that says what to do next,
 * and the action that does it (docs/design/README.md, voice).
 */

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon: LucideIcon
  title: string
  description?: React.ReactNode
  action?: React.ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center gap-3 border-t px-6 py-16 text-center",
        className,
      )}
    >
      <div className="flex size-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
        <Icon className="size-5" aria-hidden="true" />
      </div>
      <div className="flex max-w-sm flex-col gap-1">
        <h2 className="text-section font-semibold">{title}</h2>
        {description && <p className="text-ink-2">{description}</p>}
      </div>
      {action && <div className="mt-1">{action}</div>}
    </div>
  )
}

/** A failed load: the problem's title and detail, and a retry when one can help. */
export function ErrorState({
  error,
  onRetry,
  retrying = false,
  action,
  className,
}: {
  error: unknown
  onRetry?: () => void
  retrying?: boolean
  action?: React.ReactNode
  className?: string
}) {
  const { title, description } = errorMessage(error)
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center gap-3 border-t px-6 py-16 text-center",
        className,
      )}
    >
      <div className="flex size-10 items-center justify-center rounded-full bg-destructive/10 text-destructive">
        <AlertCircleIcon className="size-5" aria-hidden="true" />
      </div>
      <div className="flex max-w-md flex-col gap-1">
        <h2 className="text-section font-semibold">{title}</h2>
        {description && <p className="text-ink-2">{description}</p>}
      </div>
      <div className="mt-1 flex gap-2">
        {onRetry && (
          <Button variant="outline" onClick={onRetry} disabled={retrying}>
            <RotateCwIcon data-icon="inline-start" className={cn(retrying && "animate-spin motion-reduce:animate-none")} />
            {retrying ? "Retrying…" : "Try again"}
          </Button>
        )}
        {action}
      </div>
    </div>
  )
}
