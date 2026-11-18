"use client"

import type { KeyboardEvent, Ref } from "react"

import { Select, SelectContent, SelectItem, SelectSeparator, SelectTrigger, SelectValue } from "@/components/ui/select"
import { STAGES, STAGE_LABELS, UNREVIEWED_LABEL, type Stage } from "@/lib/applications"
import { cn } from "@/lib/utils"

/** The select's value for "no stage" (`null` in the API); Radix items need a non-empty value. */
const UNREVIEWED = "unreviewed"

/**
 * Radix Select picks the first matching item when a letter is typed on the
 * closed trigger. Here that would move a candidate to a stage by accident
 * (pressing S to focus the selector, then S again would pick "Screened"), so
 * letters on the closed trigger are ignored; Space, Enter and the arrows open it.
 */
function ignoreTypeahead(event: KeyboardEvent<HTMLButtonElement>) {
  if (event.key.length === 1 && event.key !== " " && !event.metaKey && !event.ctrlKey && !event.altKey) {
    event.preventDefault()
  }
}

/** An application's pipeline stage, including "Unreviewed" (no stage). */
export function StageSelect({
  value,
  onValueChange,
  disabled = false,
  ref,
  id,
  className,
  "aria-label": ariaLabel = "Stage",
  "aria-describedby": ariaDescribedBy,
}: {
  value: Stage | null
  onValueChange: (stage: Stage | null) => void
  disabled?: boolean
  /** The trigger, to focus it (the S shortcut). */
  ref?: Ref<HTMLButtonElement>
  id?: string
  className?: string
  "aria-label"?: string
  "aria-describedby"?: string
}) {
  return (
    <Select
      value={value ?? UNREVIEWED}
      onValueChange={(next) => onValueChange(next === UNREVIEWED ? null : (next as Stage))}
      disabled={disabled}
    >
      <SelectTrigger
        ref={ref}
        id={id}
        size="sm"
        aria-label={ariaLabel}
        aria-describedby={ariaDescribedBy}
        className={cn("min-w-36", className)}
        onKeyDown={ignoreTypeahead}
        data-stage-select=""
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent position="popper" align="start">
        <SelectItem value={UNREVIEWED}>{UNREVIEWED_LABEL}</SelectItem>
        <SelectSeparator />
        {STAGES.map((stage) => (
          <SelectItem key={stage} value={stage}>
            {STAGE_LABELS[stage]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
