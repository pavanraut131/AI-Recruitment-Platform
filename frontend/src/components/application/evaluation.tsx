import { CheckCircle2Icon, CircleAlertIcon, CircleMinusIcon, type LucideIcon } from "lucide-react"
import type { ReactNode } from "react"

import { scoreParts, type Application } from "@/lib/applications"
import { cn } from "@/lib/utils"

import { Meter, categoryBarClass } from "./score"

/*
 * The AI's evaluation of one application (spec §5.2, §5.4): score breakdown,
 * recommendation, summary, strengths, weaknesses and missing skills, plus the
 * extracted skills. The review panel and the Person profile's Evaluation
 * section render the same pieces.
 */

/** A titled block inside the panel or a profile section. */
export function EvaluationBlock({
  title,
  icon: Icon,
  iconClassName,
  children,
  className,
}: {
  title: string
  icon?: LucideIcon
  iconClassName?: string
  children: ReactNode
  className?: string
}) {
  return (
    <section aria-label={title} className={cn("flex flex-col gap-2", className)}>
      <h3 className="flex items-center gap-1.5 text-xs font-medium tracking-wide text-muted-foreground uppercase">
        {Icon && <Icon className={cn("size-3.5", iconClassName)} aria-hidden="true" />}
        {title}
      </h3>
      {children}
    </section>
  )
}

/** Sub-score bars (`score_breakdown`), each as points out of its maximum. */
export function ScoreBreakdown({ application }: { application: Pick<Application, "score_breakdown" | "category"> }) {
  const parts = scoreParts(application.score_breakdown)
  if (parts === null) {
    return <p className="text-sm text-muted-foreground">No breakdown for this score.</p>
  }
  const barClass = categoryBarClass(application.category)
  return (
    <dl className="grid grid-cols-[minmax(7rem,auto)_1fr_auto] items-center gap-x-3 gap-y-2 text-sm">
      {parts.map((part) => (
        <div key={part.key} className="contents" data-slot="score-part">
          <dt className="text-muted-foreground">{part.label}</dt>
          <dd className="contents">
            <Meter percent={(part.value / part.max) * 100} barClassName={barClass} className="h-1.5" />
            <span className="text-right tabular-nums" aria-label={`${part.value} out of ${part.max}`}>
              {formatPoints(part.value)}
              <span className="text-muted-foreground">/{part.max}</span>
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

function BulletList({ items, empty, markerClassName }: { items: string[]; empty: string; markerClassName: string }) {
  if (items.length === 0) return <p className="text-sm text-muted-foreground">{empty}</p>
  return (
    <ul className="flex flex-col gap-1.5 text-sm">
      {items.map((item, index) => (
        <li key={`${index}-${item}`} className="flex gap-2">
          <span className={cn("mt-2 size-1.5 shrink-0 rounded-full", markerClassName)} aria-hidden="true" />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  )
}

/** Skills as chips; the campaign's required skills (lower-cased) are highlighted. */
export function SkillChips({
  skills,
  required,
  empty = "No skills extracted.",
  tone = "neutral",
}: {
  skills: readonly string[]
  required?: ReadonlySet<string>
  empty?: string
  /** `missing`: skills the candidate lacks (a muted rose outline). */
  tone?: "neutral" | "missing"
}) {
  const unique = [...new Map(skills.map((skill) => [skill.trim().toLowerCase(), skill.trim()])).values()].filter(Boolean)
  if (unique.length === 0) return <p className="text-sm text-muted-foreground">{empty}</p>
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label={tone === "missing" ? "Missing skills" : "Skills"}>
      {unique.map((skill) => {
        const isRequired = required?.has(skill.toLowerCase()) ?? false
        return (
          <li
            key={skill}
            data-required={isRequired || undefined}
            className={cn(
              "inline-flex h-6 items-center rounded-sm border px-2 text-xs",
              tone === "missing"
                ? "border-category-rejected/30 bg-category-rejected-muted text-category-rejected-foreground"
                : isRequired
                  ? "border-primary/30 bg-primary/10 font-medium text-primary"
                  : "border-border text-foreground",
            )}
          >
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
 * Breakdown, recommendation, summary, strengths, weaknesses and missing skills.
 * An unscored application (still analysing, or scoring failed) says so instead.
 */
export function ApplicationEvaluation({ application }: { application: EvaluationFields }) {
  if (application.score === null) {
    return (
      <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
        Not scored yet: the resume is still being analysed, or it could not be scored.
      </p>
    )
  }
  return (
    // A container: two columns where there is room (the profile), one in the 480 px panel.
    <div className="@container flex flex-col gap-5">
      <EvaluationBlock title="Score breakdown">
        <ScoreBreakdown application={application} />
      </EvaluationBlock>
      {application.recommendation && (
        <EvaluationBlock title="Recommendation">
          <p className="rounded-md border-l-2 border-primary bg-muted/50 px-3 py-2 text-sm">{application.recommendation}</p>
        </EvaluationBlock>
      )}
      {application.summary && (
        <EvaluationBlock title="Summary">
          <p className="text-sm leading-relaxed">{application.summary}</p>
        </EvaluationBlock>
      )}
      <div className="grid gap-5 @xl:grid-cols-2">
        <EvaluationBlock title="Strengths" icon={CheckCircle2Icon} iconClassName="text-category-strong">
          <BulletList items={application.strengths} empty="None noted." markerClassName="bg-category-strong" />
        </EvaluationBlock>
        <EvaluationBlock title="Weaknesses" icon={CircleAlertIcon} iconClassName="text-category-moderate">
          <BulletList items={application.weaknesses} empty="None noted." markerClassName="bg-category-moderate" />
        </EvaluationBlock>
      </div>
      <EvaluationBlock title="Missing skills" icon={CircleMinusIcon} iconClassName="text-category-rejected">
        <SkillChips skills={application.missing_skills} tone="missing" empty="None: every required skill was found." />
      </EvaluationBlock>
    </div>
  )
}
