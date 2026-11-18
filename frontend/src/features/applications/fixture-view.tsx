"use client"

import type { RowSelectionState } from "@tanstack/react-table"
import { Profiler, useCallback, useMemo, useRef, useState, type ProfilerOnRenderCallback } from "react"

import { CandidatePreviewSheet } from "./candidate-preview-sheet"
import { CandidatesTable, rowDomId } from "./candidates-table"
import { makeApplications } from "./fixtures"
import { DEFAULT_SORT, type ApplicationSort } from "./model"
import { useRowShortcuts } from "./use-row-shortcuts"

export type FixtureViewProps = {
  /** How many synthetic rows to render (the spec's bar is 500 on a page). */
  rowCount: number
  /** Called after every React commit of the table with its render time (ms). */
  onCommit?: (phase: string, actualDurationMs: number) => void
}

/**
 * The table over synthetic rows, with selection, J/K and the preview panel
 * wired like the real tab but no API: the 500-row benchmark (Vitest) and the
 * dev route (`/dev/candidates?rows=500`). Sorting is a
 * no-op here (it is server-side in the product).
 */
export function CandidatesFixtureView({ rowCount, onCommit }: FixtureViewProps) {
  const rows = useMemo(() => makeApplications(rowCount), [rowCount])
  const requiredSkills = useMemo(() => new Set(["react", "typescript", "postgresql"]), [])
  const [sort, setSort] = useState<ApplicationSort>(DEFAULT_SORT)
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({})
  const [activeId, setActiveId] = useState<string | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)
  const readout = useRef<HTMLSpanElement>(null)

  const onActiveChange = useCallback((id: string) => {
    setActiveId(id)
    setOpenId((open) => (open === null ? null : id))
  }, [])
  const onOpen = useCallback((id: string) => {
    setActiveId(id)
    setOpenId(id)
  }, [])
  const ids = useMemo(() => rows.map((row) => row.id), [rows])
  useRowShortcuts({
    ids,
    activeId,
    onMove: (id) => {
      onActiveChange(id)
      document.getElementById(rowDomId(id))?.focus()
    },
  })

  const onRender = useCallback<ProfilerOnRenderCallback>(
    (_id, phase, actualDuration) => {
      onCommit?.(phase, actualDuration)
      // Written to the DOM directly: a state update here would re-render the profiled tree.
      if (readout.current) readout.current.textContent = `${phase}: ${actualDuration.toFixed(1)} ms`
    },
    [onCommit],
  )

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted-foreground">
        {rows.length} synthetic rows · last React commit <span ref={readout} className="font-mono tabular-nums" />
      </p>
      <div className="overflow-x-auto rounded-lg border">
        <Profiler id="candidates-table" onRender={onRender}>
          <CandidatesTable
            rows={rows}
            requiredSkills={requiredSkills}
            sort={sort}
            onSortChange={setSort}
            rowSelection={rowSelection}
            onRowSelectionChange={setRowSelection}
            activeId={activeId}
            onActiveChange={onActiveChange}
            onOpen={onOpen}
          />
        </Profiler>
      </div>
      <CandidatePreviewSheet
        open={openId !== null}
        application={rows.find((row) => row.id === openId)}
        loading={false}
        onClose={() => setOpenId(null)}
      />
    </div>
  )
}
