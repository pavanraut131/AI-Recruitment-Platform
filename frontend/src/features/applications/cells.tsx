import { useEffect, useRef } from "react"

import { mustHaves } from "@/lib/applications"
import { cn } from "@/lib/utils"

import { stageLabel, type Stage } from "./model"

// The score cell is shared with Home, the review panel and the Person profile.
export { MatchLabel, ScoreCell } from "@/components/application/score"

/**
 * "4 of 5": the campaign's must-haves the candidate has. The title lists the
 * missing ones (the review panel shows them as chips). A dash when the
 * campaign lists no required skills.
 */
export function MustHavesCell({ skills, required }: { skills: readonly string[]; required: ReadonlySet<string> }) {
  if (required.size === 0) return <span className="text-muted-foreground">—</span>
  const items = mustHaves([...required], skills)
  const has = items.filter((item) => item.has).length
  const missing = items.filter((item) => !item.has).map((item) => item.skill)
  return (
    <span
      className={cn(has === items.length ? "text-foreground" : "text-ink-2")}
      title={missing.length > 0 ? `Missing: ${missing.join(", ")}` : "Has every must-have"}
    >
      {has} of {items.length}
    </span>
  )
}

export function StageCell({ stage }: { stage: Stage | null }) {
  return <span className={cn(stage === null ? "text-muted-foreground" : "text-ink-2")}>{stageLabel(stage)}</span>
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
