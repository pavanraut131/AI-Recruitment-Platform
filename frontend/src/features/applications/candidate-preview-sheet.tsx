"use client"

import { Kbd } from "@/components/ui/kbd"
import { Separator } from "@/components/ui/separator"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"

import { CategoryBadge, ScoreCell } from "./cells"
import { formatYears, personName, stageLabel, type Application } from "./model"

/**
 * A placeholder for the review panel (spec §5.2, E1-07): name, email, score,
 * category and summary of the active row.
 *
 * Non-modal, so the table stays usable beside it: J/K keep moving the active
 * row (and the panel follows), and clicking another row switches the panel
 * instead of closing it. Esc or the close button closes it. Focus stays on the
 * table when it opens, so the keyboard flow is not interrupted.
 */
export function CandidatePreviewSheet({
  open,
  application,
  loading,
  onClose,
}: {
  open: boolean
  application: Application | undefined
  loading: boolean
  onClose: () => void
}) {
  return (
    <Sheet open={open} onOpenChange={(next) => !next && onClose()} modal={false}>
      <SheetContent
        side="right"
        className="data-[side=right]:w-full data-[side=right]:sm:max-w-[480px]"
        onOpenAutoFocus={(event) => event.preventDefault()}
        onCloseAutoFocus={(event) => event.preventDefault()}
        onInteractOutside={(event) => {
          // Clicking a row switches the panel; it should not close it first.
          if ((event.target as Element | null)?.closest?.("[data-slot='candidates-table']")) event.preventDefault()
        }}
      >
        {application ? (
          <>
            <SheetHeader className="border-b pr-12">
              <SheetTitle className="text-lg">{personName(application)}</SheetTitle>
              <SheetDescription>{application.person.email ?? "No email"}</SheetDescription>
            </SheetHeader>
            <div className="flex flex-col gap-4 overflow-y-auto px-4">
              <dl className="grid grid-cols-[8rem_1fr] items-center gap-x-4 gap-y-3 text-sm">
                <dt className="text-muted-foreground">Score</dt>
                <dd>
                  <ScoreCell score={application.score} category={application.category} />
                </dd>
                <dt className="text-muted-foreground">Category</dt>
                <dd>
                  <CategoryBadge category={application.category} />
                </dd>
                <dt className="text-muted-foreground">Stage</dt>
                <dd>{stageLabel(application.pipeline_stage)}</dd>
                <dt className="text-muted-foreground">Experience</dt>
                <dd className="tabular-nums">{formatYears(application.years_of_experience)}</dd>
              </dl>
              <Separator />
              <section aria-labelledby="candidate-summary-heading" className="flex flex-col gap-1.5">
                <h3 id="candidate-summary-heading" className="text-sm font-medium">
                  Summary
                </h3>
                <p className="text-sm leading-relaxed text-muted-foreground">
                  {application.summary ?? "No summary yet: the resume is still being analysed or could not be scored."}
                </p>
              </section>
              <p className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">
                The full review panel (score breakdown, strengths and gaps, stage and notes) arrives in E1-07.
              </p>
            </div>
            <p className="mt-auto flex items-center gap-1.5 border-t p-4 text-xs text-muted-foreground">
              <Kbd>J</Kbd>
              <Kbd>K</Kbd> next / previous candidate · <Kbd>Esc</Kbd> close
            </p>
          </>
        ) : (
          <>
            <SheetHeader className="border-b">
              <SheetTitle className="sr-only">{loading ? "Loading candidate" : "Candidate not found"}</SheetTitle>
              {loading ? (
                <>
                  <Skeleton className="h-5 w-40" />
                  <Skeleton className="h-4 w-56" />
                </>
              ) : (
                <SheetDescription>This candidate is not in this campaign any more.</SheetDescription>
              )}
            </SheetHeader>
          </>
        )}
      </SheetContent>
    </Sheet>
  )
}
