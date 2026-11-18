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
      className="flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-2 py-1"
    >
      <span className="px-1 text-sm font-medium tabular-nums" aria-live="polite">
        {count} selected
      </span>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" disabled={pending}>
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
      <Button variant="destructive" size="sm" disabled={pending} onClick={onReject}>
        <BanIcon aria-hidden="true" />
        Reject
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
    <Button variant="outline" size="sm" disabled={pending || disabled} onClick={onExport}>
      <DownloadIcon aria-hidden="true" />
      {pending ? "Exporting…" : selectedCount > 0 ? `Export ${selectedCount} selected` : "Export CSV"}
    </Button>
  )
}
