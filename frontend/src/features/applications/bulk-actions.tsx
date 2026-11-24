"use client"

import { ArrowRightIcon, BanIcon, ChevronDownIcon, DownloadIcon, XIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

import { REJECTED_STAGE, STAGES, STAGE_LABELS, type Stage } from "./model"

/** Stages offered by "Move to stage" (rejecting has its own button). */
const MOVE_STAGES = STAGES.filter((stage) => stage !== REJECTED_STAGE)

/**
 * The actions on the selected rows: move to a stage, reject, clear the
 * selection. Shown only while rows are selected.
 */
export function BulkActionsBar({
  count,
  pending,
  onMove,
  onReject,
  onClear,
}: {
  count: number
  pending: boolean
  onMove: (stage: Stage) => void
  onReject: () => void
  onClear: () => void
}) {
  return (
    <div
      role="toolbar"
      aria-label="Bulk actions"
      className="flex flex-wrap items-center gap-2 rounded-lg bg-primary-soft px-2 py-1.5"
    >
      <span className="px-1.5 text-sm font-semibold" aria-live="polite">
        {count} selected
      </span>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" disabled={pending} className="bg-background">
            <ArrowRightIcon aria-hidden="true" />
            Move to stage
            <ChevronDownIcon className="opacity-60" aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className="w-44">
          <DropdownMenuLabel className="text-xs text-muted-foreground">Move {count} to</DropdownMenuLabel>
          {MOVE_STAGES.map((stage) => (
            <DropdownMenuItem key={stage} onSelect={() => onMove(stage)}>
              {STAGE_LABELS[stage]}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      <Button variant="outline" size="sm" disabled={pending} onClick={onReject} className="bg-background">
        <BanIcon aria-hidden="true" />
        Not a fit
      </Button>
      <Button variant="ghost" size="sm" onClick={onClear}>
        <XIcon aria-hidden="true" />
        Clear selection
      </Button>
    </div>
  )
}

/** Export CSV: the selection when there is one, otherwise everything matching the filters. */
export function ExportButton({
  selectedCount,
  pending,
  disabled,
  onExport,
}: {
  selectedCount: number
  pending: boolean
  disabled?: boolean
  onExport: () => void
}) {
  return (
    <Button variant="ghost" disabled={pending || disabled} onClick={onExport} className="text-ink-2">
      <DownloadIcon aria-hidden="true" />
      {pending ? "Exporting…" : selectedCount > 0 ? `Export ${selectedCount} selected` : "Export CSV"}
    </Button>
  )
}
