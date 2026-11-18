import type { CSSProperties } from "react"

import { Badge } from "@/components/ui/badge"
import { CATEGORY_META, SCORE_BAR_CLASS, formatScore, type Category } from "@/lib/applications"
import { cn } from "@/lib/utils"

/*
 * An application's score and category, as the candidates table, the review
 * panel and the Person profile show them. Colours are category tokens; the
 * one inline style is a bar's width, set as a CSS variable (`--bar`) and used
 * by a `w-(--bar)` class (EPIC-01 §3, §11: no other inline styles).
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
    <div className={cn("h-1 overflow-hidden rounded-full bg-muted", className)} aria-hidden="true">
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

/** Score: the number plus a thin bar in the category's colour. Unscored: a dash, no bar. */
export function ScoreCell({ score, category }: { score: number | null; category: Category | null }) {
  if (score === null) {
    return (
      <span className="text-muted-foreground" aria-label="Not scored">
        —
      </span>
    )
  }
  return (
    <div className="flex items-center gap-2">
      <span className="w-7 text-right font-medium tabular-nums">{formatScore(score)}</span>
      <Meter percent={score} barClassName={categoryBarClass(category)} className="w-16" />
    </div>
  )
}

export function CategoryBadge({ category }: { category: Category | null }) {
  if (category === null) return <span className="text-muted-foreground">—</span>
  const { label, tone } = CATEGORY_META[category]
  return <Badge variant={tone}>{label}</Badge>
}
