import { useEffect, useRef, type CSSProperties } from "react"

import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

import {
  CATEGORY_META,
  SCORE_BAR_CLASS,
  formatScore,
  stageLabel,
  type Category,
  type Stage,
} from "./model"

/** Score: the number plus a thin bar in the category's colour. Unscored: a dash, no bar. */
export function ScoreCell({ score, category }: { score: number | null; category: Category | null }) {
  if (score === null) {
    return (
      <span className="text-muted-foreground" aria-label="Not scored">
        —
      </span>
    )
  }
  const percent = Math.min(100, Math.max(0, score))
  const barClass = category ? SCORE_BAR_CLASS[CATEGORY_META[category].tone] : "bg-muted-foreground"
  return (
    <div className="flex items-center gap-2">
      <span className="w-7 text-right font-medium tabular-nums">{formatScore(score)}</span>
      <div className="h-1 w-16 overflow-hidden rounded-full bg-muted" aria-hidden="true">
        {/* The width is the one dynamic value (a CSS variable); the colour is a token class. */}
        <div
          data-slot="score-bar"
          className={cn("h-full w-(--score) rounded-full", barClass)}
          style={{ "--score": `${percent}%` } as CSSProperties}
        />
      </div>
    </div>
  )
}

export function CategoryBadge({ category }: { category: Category | null }) {
  if (category === null) return <span className="text-muted-foreground">—</span>
  const { label, tone } = CATEGORY_META[category]
  return <Badge variant={tone}>{label}</Badge>
}

/**
 * The top skills: the campaign's required skills the candidate has come first
 * (highlighted), then the rest in resume order.
 */
export function topSkills(
  skills: readonly string[],
  required: ReadonlySet<string>,
  limit = 3,
): { shown: { skill: string; required: boolean }[]; more: number } {
  const unique = [...new Map(skills.map((skill) => [skill.trim().toLowerCase(), skill.trim()])).values()].filter(
    Boolean,
  )
  const matched = unique.filter((skill) => required.has(skill.toLowerCase()))
  const others = unique.filter((skill) => !required.has(skill.toLowerCase()))
  const ordered = [...matched.map((skill) => ({ skill, required: true })), ...others.map((skill) => ({ skill, required: false }))]
  return { shown: ordered.slice(0, limit), more: Math.max(0, ordered.length - limit) }
}

export function SkillsCell({ skills, required }: { skills: readonly string[]; required: ReadonlySet<string> }) {
  const { shown, more } = topSkills(skills, required)
  if (shown.length === 0) return <span className="text-muted-foreground">—</span>
  return (
    <div className="flex items-center gap-1">
      {shown.map(({ skill, required: isRequired }) => (
        <span
          key={skill}
          data-required={isRequired || undefined}
          className={cn(
            "inline-flex h-5 max-w-32 items-center truncate rounded-sm border px-1.5 text-xs",
            isRequired
              ? "border-primary/30 bg-primary/10 font-medium text-primary"
              : "border-border text-muted-foreground",
          )}
          title={isRequired ? `${skill} (required skill)` : skill}
        >
          {skill}
          {isRequired && <span className="sr-only"> (required)</span>}
        </span>
      ))}
      {more > 0 && <span className="text-xs text-muted-foreground">+{more}</span>}
    </div>
  )
}

export function StageCell({ stage }: { stage: Stage | null }) {
  return <span className={cn(stage === null && "text-muted-foreground")}>{stageLabel(stage)}</span>
}

/**
 * The selection checkbox: a native input in the accent colour. The table has
 * up to 500 of them, and the Radix checkbox (components/ui) cost about twice
 * the render and style time when selecting all (measured in the PR).
 */
export function RowCheckbox({
  checked,
  indeterminate = false,
  onCheckedChange,
  "aria-label": ariaLabel,
}: {
  checked: boolean
  indeterminate?: boolean
  onCheckedChange: (checked: boolean) => void
  "aria-label": string
}) {
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate && !checked
  }, [indeterminate, checked])
  return (
    <input
      ref={ref}
      type="checkbox"
      aria-label={ariaLabel}
      checked={checked}
      onChange={(event) => onCheckedChange(event.target.checked)}
      className="block size-4 cursor-pointer rounded-sm accent-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
    />
  )
}
