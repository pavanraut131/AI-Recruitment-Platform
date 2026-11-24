import type { CSSProperties } from "react"

import { CATEGORY_META, CATEGORY_TEXT_CLASS, SCORE_BAR_CLASS, formatScore, type Category } from "@/lib/applications"
import { cn } from "@/lib/utils"

/*
 * An application's score as every screen shows it (docs/design/README.md,
 * score display): the number, a 5-segment meter and a plain-word label
 * ("Strong match"). Colours are category tokens; the one inline style is a
 * bar's width, set as a CSS variable (`--bar`) and used by a `w-(--bar)`
 * class (no other inline styles).
 */

/** A thin horizontal bar filled to `percent` (0–100). */
export function Meter({
  percent,
  barClassName,
  className,
}: {
  percent: number
  barClassName: string
  className?: string
}) {
  const clamped = Math.min(100, Math.max(0, percent))
  return (
    <div className={cn("h-1 overflow-hidden rounded-full bg-border", className)} aria-hidden="true">
      <div
        data-slot="score-bar"
        className={cn("h-full w-(--bar) rounded-full", barClassName)}
        style={{ "--bar": `${clamped}%` } as CSSProperties}
      />
    </div>
  )
}

/** The bar colour of a score in `category` (neutral when unscored or uncategorised). */
export function categoryBarClass(category: Category | null): string {
  return category ? SCORE_BAR_CLASS[CATEGORY_META[category].tone] : "bg-muted-foreground"
}

const SEGMENTS = [0, 1, 2, 3, 4] as const

/**
 * Five segments, one per 20 points (rounded), in the category's colour: a
 * score read at a glance without reading the number. Decorative: the number
 * and the label carry the value.
 */
export function ScoreSegments({
  score,
  category,
  size = "sm",
}: {
  score: number
  category: Category | null
  size?: "sm" | "md"
}) {
  const filled = Math.round(Math.min(100, Math.max(0, score)) / 20)
  const on = categoryBarClass(category)
  return (
    <span aria-hidden="true" data-slot="score-meter" className="inline-flex gap-0.5">
      {SEGMENTS.map((index) => (
        <span
          key={index}
          className={cn(
            "rounded-[2px]",
            size === "sm" ? "h-3 w-[5px]" : "h-3.5 w-1.5",
            index < filled ? on : "bg-border",
          )}
        />
      ))}
    </span>
  )
}

/** The category in words, in its colour ("Possible match"). Uncategorised: a dash. */
export function MatchLabel({ category, className }: { category: Category | null; className?: string }) {
  if (category === null) return <span className={cn("text-muted-foreground", className)}>—</span>
  const { label, tone } = CATEGORY_META[category]
  return <span className={cn(CATEGORY_TEXT_CLASS[tone], className)}>{label}</span>
}

/**
 * Score: the number and the segment meter, plus the label when `withLabel`
 * (the candidates table, Home's queue). Unscored: a dash, no meter.
 */
export function ScoreCell({
  score,
  category,
  withLabel = false,
  size = "sm",
}: {
  score: number | null
  category: Category | null
  withLabel?: boolean
  size?: "sm" | "md"
}) {
  if (score === null) {
    return (
      <span className="text-muted-foreground" aria-label="Not scored">
        —
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap">
      <span className={cn("w-6 text-right font-semibold", size === "md" && "text-section")}>{formatScore(score)}</span>
      <ScoreSegments score={score} category={category} size={size} />
      {withLabel && <MatchLabel category={category} className="text-dense" />}
    </span>
  )
}

/** The review panel's and profile's focal score: the big number and its label. */
export function ScoreHeadline({ score, category }: { score: number | null; category: Category | null }) {
  if (score === null) return null
  return (
    <p className="flex items-baseline gap-2.5">
      <span className="text-score font-semibold tracking-tight">{formatScore(score)}</span>
      <MatchLabel category={category} className="font-semibold" />
    </p>
  )
}
