import { CheckIcon, XIcon } from "lucide-react"
import type { ReactNode } from "react"

import { mustHaves, scoreParts, type Application } from "@/lib/applications"
import { cn } from "@/lib/utils"

import { Meter, ScoreHeadline } from "./score"

/*
 * The evaluation of one application (spec §5.2, §5.4): the score and its
 * label, the recommendation, the must-haves, how the score adds up,
 * strengths, gaps and missing skills. The review panel and the Person
 * profile's Evaluation section render the same pieces.
 */

/** A titled block inside the panel or a profile section: a small grey heading, no box. */
export function EvaluationBlock({
  title,
  children,
  className,
  action,
}: {
  title: string
  children: ReactNode
  className?: string
  action?: ReactNode
}) {
  return (
    <section aria-label={title} className={cn("flex flex-col gap-2", className)}>
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-dense font-semibold text-muted-foreground">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  )
}

/** "How the score adds up": thin neutral bars, each as points out of its maximum. */
export function ScoreBreakdown({ application }: { application: Pick<Application, "score_breakdown" | "category"> }) {
  const parts = scoreParts(application.score_breakdown)
  if (parts === null) {
    return <p className="text-dense text-muted-foreground">No breakdown for this score.</p>
  }
  return (
    <dl className="grid max-w-xl grid-cols-[minmax(7rem,9rem)_minmax(0,1fr)_3.5rem] items-center gap-x-3 gap-y-2 text-dense">
      {parts.map((part) => (
        <div key={part.key} className="contents" data-slot="score-part">
          <dt className="text-ink-2">{part.label}</dt>
          <dd className="contents">
            <Meter percent={(part.value / part.max) * 100} barClassName="bg-ink-2" />
            <span className="text-right text-ink-2" aria-label={`${formatPoints(part.value)} out of ${part.max}`}>
              {formatPoints(part.value)} / {part.max}
            </span>
          </dd>
        </div>
      ))}
    </dl>
  )
}

function formatPoints(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

/** The recommendation, then the summary, as one short paragraph. */
export function Recommendation({ application }: { application: Pick<Application, "recommendation" | "summary"> }) {
  const recommendation = application.recommendation?.trim()
  const summary = application.summary?.trim()
  if (!recommendation && !summary) return null
  return (
    <p className="max-w-prose text-ink-2">
      {recommendation && (
        <>
          <span className="font-medium text-foreground">{recommendation}</span>
          {/[.!?]$/.test(recommendation) ? "" : "."}
        </>
      )}
      {recommendation && summary && " "}
      {summary}
    </p>
  )
}

/** The campaign's required skills as chips: a check for each the candidate has, a cross for each they lack. */
export function MustHaveChips({ required, skills }: { required: readonly string[]; skills: readonly string[] }) {
  const items = mustHaves(required, skills)
  if (items.length === 0) {
    return <p className="text-dense text-muted-foreground">This campaign lists no required skills.</p>
  }
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Must-haves">
      {items.map(({ skill, has }) => (
        <li
          key={skill}
          data-has={has || undefined}
          className={cn(
            "inline-flex h-7 items-center gap-1.5 rounded-full border border-input px-2.5 text-dense",
            !has && "text-muted-foreground",
          )}
        >
          {has ? (
            <CheckIcon className="size-3.5 text-category-strong" strokeWidth={2.6} aria-hidden="true" />
          ) : (
            <XIcon className="size-3.5 text-destructive" strokeWidth={2.6} aria-hidden="true" />
          )}
          {skill}
          <span className="sr-only">{has ? " (has it)" : " (missing)"}</span>
        </li>
      ))}
    </ul>
  )
}

function BulletList({ items, empty, markerClassName }: { items: string[]; empty: string; markerClassName: string }) {
  if (items.length === 0) return <p className="text-dense text-muted-foreground">{empty}</p>
  return (
    <ul className="flex flex-col gap-1.5 text-ink-2">
      {items.map((item, index) => (
        <li key={`${index}-${item}`} className="flex gap-2.5">
          <span className={cn("mt-2 size-1.5 shrink-0 rounded-full", markerClassName)} aria-hidden="true" />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  )
}

/** Skills as quiet chips; the campaign's required skills (lower-cased) are marked. */
export function SkillChips({
  skills,
  required,
  empty = "No skills found on the resume.",
  tone = "neutral",
}: {
  skills: readonly string[]
  required?: ReadonlySet<string>
  empty?: string
  /** `missing`: skills the candidate lacks (crossed). */
  tone?: "neutral" | "missing"
}) {
  const unique = [...new Map(skills.map((skill) => [skill.trim().toLowerCase(), skill.trim()])).values()].filter(Boolean)
  if (unique.length === 0) return <p className="text-dense text-muted-foreground">{empty}</p>
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label={tone === "missing" ? "Missing skills" : "Skills"}>
      {unique.map((skill) => {
        const isRequired = required?.has(skill.toLowerCase()) ?? false
        return (
          <li
            key={skill}
            data-required={isRequired || undefined}
            className={cn(
              "inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-dense",
              tone === "missing"
                ? "border-input text-muted-foreground"
                : isRequired
                  ? "border-primary/30 bg-primary-soft font-medium text-foreground"
                  : "border-input text-ink-2",
            )}
          >
            {tone === "missing" && <XIcon className="size-3.5 text-destructive" strokeWidth={2.6} aria-hidden="true" />}
            {skill}
            {isRequired && <span className="sr-only"> (required)</span>}
          </li>
        )
      })}
    </ul>
  )
}

type EvaluationFields = Pick<
  Application,
  "score" | "score_breakdown" | "category" | "summary" | "recommendation" | "strengths" | "weaknesses" | "missing_skills"
>

/**
 * The full evaluation (the Person profile): score and label, recommendation,
 * how the score adds up, strengths, gaps and missing skills. An unscored
 * application (still being read, or scoring failed) says so instead.
 */
export function ApplicationEvaluation({ application }: { application: EvaluationFields }) {
  if (application.score === null) {
    return (
      <p className="text-ink-2">
        Not scored yet. The resume is still being read, or it could not be scored: check the campaign&apos;s Uploads tab.
      </p>
    )
  }
  return (
    // A container: two columns where there is room (the profile), one in a narrow column.
    <div className="@container flex flex-col gap-6">
      <div className="flex flex-col gap-2.5">
        <ScoreHeadline score={application.score} category={application.category} />
        <Recommendation application={application} />
      </div>
      <EvaluationBlock title="How the score adds up">
        <ScoreBreakdown application={application} />
      </EvaluationBlock>
      <div className="grid gap-6 @xl:grid-cols-2">
        <EvaluationBlock title="Strengths">
          <BulletList items={application.strengths} empty="None noted." markerClassName="bg-category-strong" />
        </EvaluationBlock>
        <EvaluationBlock title="Gaps">
          <BulletList items={application.weaknesses} empty="None noted." markerClassName="bg-category-moderate" />
        </EvaluationBlock>
      </div>
      <EvaluationBlock title="Missing skills">
        <SkillChips skills={application.missing_skills} tone="missing" empty="None. Every required skill is on the resume." />
      </EvaluationBlock>
    </div>
  )
}
