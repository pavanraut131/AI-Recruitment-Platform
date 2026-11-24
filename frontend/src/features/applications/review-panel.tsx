"use client"

import { useQuery } from "@tanstack/react-query"
import { MailIcon, SearchXIcon, UserRoundIcon } from "lucide-react"
import Link from "next/link"
import { useId, useRef, type ReactNode, type RefObject } from "react"

import { ApplicationEvaluation, EvaluationBlock, SkillChips } from "@/components/application/evaluation"
import { ApplicationNotes, ApplicationStageSelect } from "@/components/application/review-controls"
import { MatchLabel, ScoreCell } from "@/components/application/score"
import { Button } from "@/components/ui/button"
import { Kbd } from "@/components/ui/kbd"
import { Separator } from "@/components/ui/separator"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { errorMessage, isApiProblem } from "@/lib/api/client"
import { formatDate, formatYears, stageLabel } from "@/lib/applications"
import { useFocusShortcut } from "@/lib/keyboard"
import { personQueryOptions } from "@/lib/query/people"
import { applicationHref, personHref } from "@/lib/routes"

import { personName, type Application } from "./model"

export type ReviewPanelProps = {
  open: boolean
  application: Application | undefined
  loading: boolean
  /** Loading the application failed (`undefined`: no error). A 404 reads as "not in this campaign". */
  error?: unknown
  onRetry?: () => void
  onClose: () => void
  /** The campaign's required skills, lower-cased (highlighted among the skills). */
  requiredSkills?: ReadonlySet<string>
}

/**
 * The review panel (spec §5.2): a non-modal right sheet beside the table with
 * the candidate's contact, the AI's evaluation, the stage selector and notes,
 * and the Person's other applications.
 *
 * Non-modal, so the table stays usable beside it: J/K keep moving the active
 * row (and the panel follows), and clicking another row switches the panel
 * instead of closing it. Esc or the close button closes it. Focus stays on the
 * table when it opens, so the keyboard flow is not interrupted; S moves it to
 * the stage selector.
 *
 * The notes keep their draft inside the notes field, so typing re-renders the
 * field only, never the table (EPIC-01 §8).
 */
export function ReviewPanel({
  open,
  application,
  loading,
  error,
  onRetry,
  onClose,
  requiredSkills,
}: ReviewPanelProps) {
  const stageTrigger = useRef<HTMLButtonElement>(null)
  // S focuses the stage selector while the panel shows an application (spec §5.1).
  useFocusShortcut("s", stageTrigger, open && application !== undefined)

  return (
    <Sheet open={open} onOpenChange={(next) => !next && onClose()} modal={false}>
      <SheetContent
        side="right"
        className="data-[side=right]:w-full data-[side=right]:sm:max-w-[480px]"
        data-panel="review"
        onOpenAutoFocus={(event) => event.preventDefault()}
        onCloseAutoFocus={(event) => event.preventDefault()}
        onInteractOutside={(event) => {
          // Clicking a row switches the panel; it should not close it first.
          if ((event.target as Element | null)?.closest?.("[data-slot='candidates-table']")) event.preventDefault()
        }}
      >
        {application ? (
          <ReviewContent application={application} requiredSkills={requiredSkills} stageTrigger={stageTrigger} />
        ) : (
          <PanelState loading={loading} error={error} onRetry={onRetry} />
        )}
      </SheetContent>
    </Sheet>
  )
}

function PanelState({ loading, error, onRetry }: { loading: boolean; error: unknown; onRetry?: () => void }) {
  if (loading) {
    return (
      <SheetHeader className="gap-2 border-b" aria-busy="true">
        <SheetTitle className="sr-only">Loading candidate</SheetTitle>
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-4 w-56" />
        <Skeleton className="mt-4 h-24 w-full" />
      </SheetHeader>
    )
  }
  const missing = error === undefined || (isApiProblem(error) && (error.status === 404 || error.status === 422))
  if (missing) {
    return (
      <SheetHeader className="items-center gap-2 py-10 text-center">
        <SearchXIcon className="size-5 text-muted-foreground" aria-hidden="true" />
        <SheetTitle>Candidate not found</SheetTitle>
        <SheetDescription>This candidate is not in this campaign any more, or the link is wrong.</SheetDescription>
      </SheetHeader>
    )
  }
  const { title, description } = errorMessage(error)
  return (
    <SheetHeader className="items-center gap-2 py-10 text-center" role="alert">
      <SheetTitle>Could not load the candidate: {title}</SheetTitle>
      {description && <SheetDescription>{description}</SheetDescription>}
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry} className="mt-2">
          Retry
        </Button>
      )}
    </SheetHeader>
  )
}

function ReviewContent({
  application,
  requiredSkills,
  stageTrigger,
}: {
  application: Application
  requiredSkills?: ReadonlySet<string>
  stageTrigger: RefObject<HTMLButtonElement | null>
}) {
  const stageId = useId()
  const stageHintId = useId()
  const target = { applicationId: application.id, campaignId: application.campaign_id, personId: application.person.id }
  const email = application.person.email

  return (
    <>
      <SheetHeader className="gap-1 border-b pr-12">
        <SheetTitle className="text-lg">{personName(application)}</SheetTitle>
        <SheetDescription className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {email ? (
            <a href={`mailto:${email}`} className="inline-flex items-center gap-1 hover:text-foreground hover:underline">
              <MailIcon className="size-3.5" aria-hidden="true" />
              {email}
            </a>
          ) : (
            <span>No email</span>
          )}
        </SheetDescription>
        <div className="mt-2">
          <Button asChild variant="outline" size="sm">
            <Link href={personHref(application.person.id, application.id)}>
              <UserRoundIcon aria-hidden="true" />
              Open full profile
            </Link>
          </Button>
        </div>
      </SheetHeader>

      <div className="flex flex-col gap-5 overflow-y-auto px-4 pb-4">
        <dl className="grid grid-cols-[7rem_1fr] items-center gap-x-4 gap-y-2.5 text-sm">
          <dt className="text-muted-foreground">Score</dt>
          <dd>
            <ScoreCell score={application.score} category={application.category} />
          </dd>
          <dt className="text-muted-foreground">Category</dt>
          <dd>
            <MatchLabel category={application.category} />
          </dd>
          <dt className="text-muted-foreground">Experience</dt>
          <dd className="tabular-nums">{formatYears(application.years_of_experience)}</dd>
          <dt>
            <label htmlFor={stageId} className="text-muted-foreground">
              Stage
            </label>
          </dt>
          <dd className="flex items-center gap-2">
            <ApplicationStageSelect
              id={stageId}
              target={target}
              stage={application.pipeline_stage}
              triggerRef={stageTrigger}
              aria-describedby={stageHintId}
            />
            <span id={stageHintId} className="text-xs text-muted-foreground">
              <Kbd>S</Kbd>
              <span className="sr-only"> focuses the stage</span>
            </span>
          </dd>
        </dl>

        <Separator />
        <ApplicationEvaluation application={application} />
        <EvaluationBlock title="Skills">
          <SkillChips skills={application.skills} required={requiredSkills} />
        </EvaluationBlock>

        <Separator />
        {/* Keyed: switching candidates saves the previous notes and starts from the next one's. */}
        <ApplicationNotes key={application.id} target={target} notes={application.notes} />

        <Separator />
        <AlsoAppliedTo personId={application.person.id} applicationId={application.id} />
      </div>

      <p className="mt-auto flex flex-wrap items-center gap-1.5 border-t p-4 text-xs text-muted-foreground">
        <Kbd>J</Kbd>
        <Kbd>K</Kbd> next / previous · <Kbd>S</Kbd> stage · <Kbd>Esc</Kbd> close
      </p>
    </>
  )
}

/** The Person's other applications, each linking to its campaign with its panel open. */
function AlsoAppliedTo({ personId, applicationId }: { personId: string; applicationId: string }) {
  const person = useQuery(personQueryOptions(personId))

  let body: ReactNode
  if (person.isPending) {
    body = (
      <div className="flex flex-col gap-2" aria-busy="true">
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
      </div>
    )
  } else if (person.isError) {
    body = (
      <div className="flex items-center justify-between gap-2 text-sm text-muted-foreground" role="alert">
        <span>Could not load their other applications.</span>
        <Button variant="outline" size="xs" onClick={() => void person.refetch()} disabled={person.isFetching}>
          Retry
        </Button>
      </div>
    )
  } else {
    const others = person.data.applications.filter((item) => item.id !== applicationId)
    body =
      others.length === 0 ? (
        <p className="text-sm text-muted-foreground">No other applications.</p>
      ) : (
        <ul className="flex flex-col divide-y rounded-md border text-sm">
          {others.map((item) => (
            <li key={item.id} className="flex items-center gap-3 px-3 py-2">
              <div className="flex min-w-0 flex-1 flex-col">
                <Link
                  href={applicationHref(item.campaign.id, item.id)}
                  className="truncate font-medium hover:underline focus-visible:underline"
                >
                  {item.campaign.title}
                </Link>
                <span className="text-xs text-muted-foreground">
                  {stageLabel(item.pipeline_stage)} · applied {formatDate(item.created_at)}
                  {item.campaign.status !== "active" && ` · campaign ${item.campaign.status}`}
                </span>
              </div>
              <ScoreCell score={item.score} category={item.category} />
            </li>
          ))}
        </ul>
      )
  }

  return (
    <EvaluationBlock title="Also applied to">
      {body}
    </EvaluationBlock>
  )
}
