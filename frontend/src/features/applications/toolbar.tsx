"use client"

import { ChevronDownIcon, SearchIcon, XIcon } from "lucide-react"
import { useEffect, useId, useRef, useState, type ComponentProps, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import { Label } from "@/components/ui/label"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { cn } from "@/lib/utils"

import { CATEGORIES, CATEGORY_DOT_CLASS, CATEGORY_META, STAGES, STAGE_LABELS } from "./model"
import { categoryToSlug, type CandidatesUrlState, type FilterPatch } from "./url-state"

/** Search and skill inputs write to the URL (and so fetch) once typing pauses this long. */
export const FILTER_DEBOUNCE_MS = 300

/**
 * A text input whose value is committed after a pause (or at once on Enter).
 * It follows `value` when it changes from outside (e.g. "Clear filters").
 */
function DebouncedInput({
  value,
  onCommit,
  delayMs = FILTER_DEBOUNCE_MS,
  ...props
}: { value: string; onCommit: (value: string) => void; delayMs?: number } & Omit<
  ComponentProps<typeof InputGroupInput>,
  "value" | "onChange"
>) {
  const [draft, setDraft] = useState(value)
  const [synced, setSynced] = useState(value)
  // Adopt an outside change (React's "adjust state on prop change" pattern).
  if (value !== synced) {
    setSynced(value)
    setDraft(value)
  }
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const commit = useRef(onCommit)
  useEffect(() => {
    commit.current = onCommit
  })
  useEffect(() => () => clearTimeout(timer.current), [])

  return (
    <InputGroupInput
      {...props}
      value={draft}
      onChange={(event) => {
        const next = event.target.value
        setDraft(next)
        clearTimeout(timer.current)
        timer.current = setTimeout(() => commit.current(next), delayMs)
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          clearTimeout(timer.current)
          commit.current(draft)
        }
        props.onKeyDown?.(event)
      }}
    />
  )
}

type FacetOption<Value extends string> = { value: Value; label: string; dotClass?: string }

/** A multi-select filter in a dropdown (categories, stages). */
function FacetFilter<Value extends string>({
  label,
  options,
  selected,
  onChange,
}: {
  label: string
  options: FacetOption<Value>[]
  selected: readonly Value[]
  onChange: (selected: Value[]) => void
}) {
  const count = selected.length
  const summary =
    count === 0 ? null : count === 1 ? options.find((option) => option.value === selected[0])?.label : `${count} selected`
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className={cn(count > 0 && "border-primary/40")}>
          {label}
          {/* A space for the accessible name ("Category · 2 selected"); flex layout ignores it. */}
          {summary && " "}
          {summary && <span className="font-normal text-muted-foreground">· {summary}</span>}
          <ChevronDownIcon className="opacity-60" aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-48" aria-label={`${label} filter`}>
        {options.map((option) => (
          <DropdownMenuCheckboxItem
            key={option.value}
            checked={selected.includes(option.value)}
            // Keep the menu open to pick several.
            onSelect={(event) => event.preventDefault()}
            onCheckedChange={(checked) =>
              onChange(
                checked
                  ? options.map((o) => o.value).filter((v) => v === option.value || selected.includes(v))
                  : selected.filter((value) => value !== option.value),
              )
            }
          >
            {option.dotClass && <span className={cn("size-2 rounded-full", option.dotClass)} aria-hidden="true" />}
            {option.label}
          </DropdownMenuCheckboxItem>
        ))}
        {count > 0 && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => onChange([])}>Clear {label.toLowerCase()}</DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function parseScore(value: string): number | null {
  if (value.trim() === "") return null
  const number = Number(value)
  if (!Number.isFinite(number)) return null
  return Math.round(Math.min(100, Math.max(0, number)))
}

/** The score range's fields; mounted each time the popover opens, so it starts from the URL. */
function ScoreRangeForm({
  min,
  max,
  onApply,
}: {
  min: number | null
  max: number | null
  onApply: (range: { min: number | null; max: number | null }) => void
}) {
  const id = useId()
  const [draftMin, setDraftMin] = useState(min === null ? "" : String(min))
  const [draftMax, setDraftMax] = useState(max === null ? "" : String(max))
  const submit = (event: FormEvent) => {
    event.preventDefault()
    let nextMin = parseScore(draftMin)
    let nextMax = parseScore(draftMax)
    if (nextMin !== null && nextMax !== null && nextMin > nextMax) [nextMin, nextMax] = [nextMax, nextMin]
    onApply({ min: nextMin, max: nextMax })
  }
  return (
    <form onSubmit={submit} className="flex flex-col gap-3" aria-label="Score range">
      <div className="flex items-end gap-2">
        <div className="flex flex-1 flex-col gap-1.5">
          <Label htmlFor={`${id}-min`} className="text-xs">
            Min score
          </Label>
          <Input
            id={`${id}-min`}
            type="number"
            inputMode="numeric"
            min={0}
            max={100}
            placeholder="0"
            value={draftMin}
            onChange={(event) => setDraftMin(event.target.value)}
          />
        </div>
        <span className="pb-2 text-muted-foreground">–</span>
        <div className="flex flex-1 flex-col gap-1.5">
          <Label htmlFor={`${id}-max`} className="text-xs">
            Max score
          </Label>
          <Input
            id={`${id}-max`}
            type="number"
            inputMode="numeric"
            min={0}
            max={100}
            placeholder="100"
            value={draftMax}
            onChange={(event) => setDraftMax(event.target.value)}
          />
        </div>
      </div>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={() => onApply({ min: null, max: null })}>
          Reset
        </Button>
        <Button type="submit" size="sm">
          Apply
        </Button>
      </div>
    </form>
  )
}

function ScoreRangeFilter({
  min,
  max,
  onChange,
}: {
  min: number | null
  max: number | null
  onChange: (range: { min: number | null; max: number | null }) => void
}) {
  const [open, setOpen] = useState(false)
  const active = min !== null || max !== null
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className={cn(active && "border-primary/40")}>
          Score
          {active && " "}
          {active && (
            <span className="font-normal text-muted-foreground tabular-nums">
              · {min ?? 0}–{max ?? 100}
            </span>
          )}
          <ChevronDownIcon className="opacity-60" aria-hidden="true" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64">
        <ScoreRangeForm
          min={min}
          max={max}
          onApply={(range) => {
            onChange(range)
            setOpen(false)
          }}
        />
      </PopoverContent>
    </Popover>
  )
}

const CATEGORY_OPTIONS = CATEGORIES.map((category) => ({
  value: categoryToSlug(category),
  label: CATEGORY_META[category].label,
  dotClass: CATEGORY_DOT_CLASS[CATEGORY_META[category].tone],
}))

const STAGE_OPTIONS = STAGES.map((stage) => ({ value: stage, label: STAGE_LABELS[stage] }))

/** Search, category, score range, skill and stage filters, plus "Clear filters" (spec §5.1). */
export function CandidatesToolbar({
  state,
  onFiltersChange,
  onClearFilters,
  hasFilters,
}: {
  state: Pick<CandidatesUrlState, "q" | "cat" | "min" | "max" | "skill" | "stage">
  onFiltersChange: (patch: FilterPatch) => void
  onClearFilters: () => void
  hasFilters: boolean
}) {
  return (
    <div className="flex flex-wrap items-center gap-2" role="search" aria-label="Filter candidates">
      <InputGroup className="h-7 w-64">
        <InputGroupAddon>
          <SearchIcon aria-hidden="true" />
        </InputGroupAddon>
        <DebouncedInput
          type="search"
          aria-label="Search candidates by name or email"
          placeholder="Search name or email"
          value={state.q}
          maxLength={200}
          onCommit={(q) => onFiltersChange({ q })}
        />
      </InputGroup>
      <FacetFilter
        label="Category"
        options={CATEGORY_OPTIONS}
        selected={state.cat}
        onChange={(cat) => onFiltersChange({ cat })}
      />
      <ScoreRangeFilter min={state.min} max={state.max} onChange={(range) => onFiltersChange(range)} />
      <InputGroup className="h-7 w-40">
        <DebouncedInput
          aria-label="Filter by skill"
          placeholder="Skill"
          value={state.skill}
          maxLength={100}
          onCommit={(skill) => onFiltersChange({ skill })}
        />
      </InputGroup>
      <FacetFilter
        label="Stage"
        options={STAGE_OPTIONS}
        selected={state.stage}
        onChange={(stage) => onFiltersChange({ stage })}
      />
      {hasFilters && (
        <Button variant="ghost" size="sm" onClick={onClearFilters}>
          <XIcon aria-hidden="true" />
          Clear filters
        </Button>
      )}
    </div>
  )
}
