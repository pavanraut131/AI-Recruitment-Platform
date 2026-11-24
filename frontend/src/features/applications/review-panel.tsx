"use client"

import { useQuery, type UseQueryResult } from "@tanstack/react-query"
import { SearchXIcon } from "lucide-react"
import Link from "next/link"
import { useId, useRef, type ReactNode, type RefObject } from "react"
import { toast } from "sonner"

import { EvaluationBlock, MustHaveChips, Recommendation, ScoreBreakdown } from "@/components/application/evaluation"
import { ApplicationNotes, ApplicationStageSelect } from "@/components/application/review-controls"
import { ScoreCell, ScoreHeadline } from "@/components/application/score"
import { Button } from "@/components/ui/button"
import { Kbd } from "@/components/ui/kbd"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { errorMessage, isApiProblem, type Schema } from "@/lib/api/client"
import { NOT_A_FIT_STAGE, formatDate, nextStep, stageLabel, type Stage } from "@/lib/applications"
import { useFocusShortcut } from "@/lib/keyboard"
import { useUpdateApplicationStage, type ApplicationRef } from "@/lib/query/applications"
import { personQueryOptions } from "@/lib/query/people"
import { workEntries, workLine } from "@/lib/profile"
import { applicationHref, personHref } from "@/lib/routes"

import { personName, type Application } from "./model"

type Person = Schema<"Person">

export type ReviewPanelProps = {
  open: boolean
  application: Application | undefined
  loading: boolean
  /** Loading the application failed (`undefined`: no error). A 404 reads as "not in this campaign". */
  error?: unknown
  onRetry?: () => void
  onClose: () => void
  /** The campaign's required skills, as written (the must-have chips). */
  requiredSkills?: readonly string[]
}

const NO_SKILLS: readonly string[] = []

/**
 * The review panel (spec §5.2, docs/design/source/Candidates.dc.html): a
 * non-modal right sheet beside the table. Who the candidate is and how to
 * reach them, then the decision (move to the next stage, or "Not a fit"),
 * then the evidence: the score and its label, the recommendation, the
 * must-haves, how the score adds up, recent experience, notes and the
 * Person's other applications.
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
  requiredSkills = NO_SKILLS,
}: ReviewPanelProps) {
  const stageTrigger = useRef<HTMLButtonElement>(null)
  // S focuses the stage selector while the panel shows an application (spec §5.1).
  useFocusShortcut("s", stageTrigger, open && application !== undefined)

  return (
    <Sheet open={open} onOpenChange={(next) => !next && onClose()} modal={false}>
      <SheetContent
        side="right"
        className="gap-0 bg-background data-[side=right]:w-full data-[side=right]:sm:max-w-[440px]"
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
      <SheetHeader className="gap-2 px-7 pt-7" aria-busy="true">
        <SheetTitle className="sr-only">Loading candidate</SheetTitle>
        <Skeleton className="h-6 w-44" />
        <Skeleton className="h-4 w-56" />
        <div className="mt-4 flex gap-2">
          <Skeleton className="h-9 w-40" />
          <Skeleton className="h-9 w-24" />
        </div>
        <Skeleton className="mt-6 h-9 w-36" />
        <Skeleton className="h-16 w-full" />
      </SheetHeader>
    )
  }
  const missing = error === undefined || (isApiProblem(error) && (error.status === 404 || error.status === 422))
  if (missing) {
    return (
      <SheetHeader className="items-center gap-2 px-7 py-14 text-center">
        <SearchXIcon className="size-5 text-muted-foreground" aria-hidden="true" />
        <SheetTitle className="text-section font-semibold">Candidate not found</SheetTitle>
        <SheetDescription className="text-ink-2">
          They are not in this campaign any more, or the link is wrong. Pick someone from the table.
        </SheetDescription>
      </SheetHeader>
    )
  }
  const { title, description } = errorMessage(error)
  return (
    <SheetHeader className="items-center gap-2 px-7 py-14 text-center" role="alert">
      <SheetTitle className="text-section font-semibold">Could not load the candidate: {title}</SheetTitle>
      {description && <SheetDescription className="text-ink-2">{description}</SheetDescription>}
      {onRetry && (
        <Button variant="outline" onClick={onRetry} className="mt-2">
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
  requiredSkills: readonly string[]
  stageTrigger: RefObject<HTMLButtonElement | null>
}) {
  const stageId = useId()
  const stageHintId = useId()
  const target = { applicationId: application.id, campaignId: application.campaign_id, personId: application.person.id }
  const email = application.person.email
  // The Person (profile and other applications): the current role, recent experience, "Also applied to".
  const person = useQuery(personQueryOptions(application.person.id))
  const latest = person.data ? workEntries(person.data.profile.work_experience)[0] : undefined
  const currentRole = latest ? workLine(latest) : null

  return (
    <>
      <SheetHeader className="gap-0.5 px-7 pt-7 pr-14 pb-0">
        <SheetTitle className="text-xl font-semibold tracking-tight">{personName(application)}</SheetTitle>
        <SheetDescription asChild>
          <div className="flex flex-col gap-0.5 text-sm">
            {currentRole && <span className="text-ink-2">{currentRole}</span>}
            {email ? (
              <a href={`mailto:${email}`} className="text-link w-fit text-dense font-normal">
                {email}
              </a>
            ) : (
              <span className="text-dense">No email on the resume</span>
            )}
          </div>
        </SheetDescription>
      </SheetHeader>

      <div className="flex flex-col gap-6 overflow-y-auto px-7 pt-5 pb-7">
        <Decision application={application} target={target} />

        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <label htmlFor={stageId} className="text-dense font-semibold text-muted-foreground">
            Stage
          </label>
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
        </div>

        {application.score === null ? (
          <p className="text-ink-2">
            Not scored yet. The resume is still being read, or it could not be scored: check the Uploads tab.
          </p>
        ) : (
          <>
            <section aria-label="Score" className="flex flex-col gap-2.5">
              <ScoreHeadline score={application.score} category={application.category} />
              <Recommendation application={application} />
            </section>
            <EvaluationBlock title="Must-haves">
              <MustHaveChips required={requiredSkills} skills={application.skills} />
            </EvaluationBlock>
            <EvaluationBlock title="How the score adds up">
              <ScoreBreakdown application={application} />
            </EvaluationBlock>
          </>
        )}

        <RecentExperience person={person} />

        {/* Keyed: switching candidates saves the previous notes and starts from the next one's. */}
        <ApplicationNotes key={application.id} target={target} notes={application.notes} />

        <AlsoAppliedTo person={person} applicationId={application.id} />
      </div>

      <p className="mt-auto flex flex-wrap items-center gap-1.5 border-t px-7 py-3 text-xs text-muted-foreground">
        <Kbd>J</Kbd>
        <Kbd>K</Kbd> next and previous · <Kbd>S</Kbd> stage · <Kbd>Esc</Kbd> close
      </p>
    </>
  )
}

/**
 * The decision: move to the next stage in the pipeline (named after it), or
 * "Not a fit". Both save at once (optimistically, like the stage selector) and
 * say so in a toast with Undo.
 */
function Decision({ application, target }: { application: Application; target: ApplicationRef }) {
  const update = useUpdateApplicationStage()
  const current = application.pipeline_stage
  const next = nextStep(current)
  const name = personName(application)

  const move = (stage: Stage, message: string) =>
    update.mutate(
      { ...target, stage },
      {
        onSuccess: () =>
          toast.success(message, {
            action: { label: "Undo", onClick: () => update.mutate({ ...target, stage: current }) },
          }),
      },
    )

  return (
    <div className="flex flex-wrap items-center gap-2">
      {next && (
        <Button onClick={() => move(next.stage, `${name} moved to ${stageLabel(next.stage)}`)} disabled={update.isPending}>
          {next.label}
        </Button>
      )}
      {current === NOT_A_FIT_STAGE ? (
        <span className="text-dense text-muted-foreground">Marked not a fit. Change the stage below to bring them back.</span>
      ) : (
        <Button
          variant="outline"
          onClick={() => move(NOT_A_FIT_STAGE, `${name} marked not a fit`)}
          disabled={update.isPending}
        >
          Not a fit
        </Button>
      )}
      <Button asChild variant="link" className="px-1.5">
        <Link href={personHref(application.person.id, application.id)}>Open full profile</Link>
      </Button>
    </div>
  )
}

type PersonQuery = UseQueryResult<Person, Error>

/** The two latest roles from the Person's extracted profile (their most recent resume). */
function RecentExperience({ person }: { person: PersonQuery }) {
  if (person.isPending) {
    return (
      <EvaluationBlock title="Recent experience">
        <div className="flex flex-col gap-2" aria-busy="true">
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      </EvaluationBlock>
    )
  }
  // A failed load is reported once, under "Also applied to".
  if (person.isError) return null
  const entries = workEntries(person.data.profile.work_experience).slice(0, 2)
  return (
    <EvaluationBlock title="Recent experience">
      {entries.length === 0 ? (
        <p className="text-dense text-muted-foreground">No work history found on the resume.</p>
      ) : (
        <ul className="flex flex-col gap-2.5">
          {entries.map((entry, index) => (
            <li key={index} className="flex flex-col gap-0.5">
              <span className="font-medium">{workLine(entry)}</span>
              {(entry.duration || entry.description) && (
                <span className="line-clamp-2 text-dense text-muted-foreground">
                  {[entry.duration, entry.description].filter(Boolean).join(" · ")}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </EvaluationBlock>
  )
}

/** The Person's other applications, each linking to its campaign with its panel open. */
function AlsoAppliedTo({ person, applicationId }: { person: PersonQuery; applicationId: string }) {
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
      <div className="flex items-center justify-between gap-2 text-dense text-muted-foreground" role="alert">
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
        <p className="text-dense text-muted-foreground">No other applications.</p>
      ) : (
        <ul className="flex flex-col border-t text-sm">
          {others.map((item) => (
            <li key={item.id} className="flex items-center gap-3 border-b py-2.5">
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

  return <EvaluationBlock title="Also applied to">{body}</EvaluationBlock>
}
