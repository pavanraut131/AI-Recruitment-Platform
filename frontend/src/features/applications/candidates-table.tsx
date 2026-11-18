"use client"

import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type OnChangeFn,
  type Row,
  type RowSelectionState,
} from "@tanstack/react-table"
import { ArrowDownIcon, ArrowUpIcon, ChevronsUpDownIcon } from "lucide-react"
import { memo, useMemo, type KeyboardEvent, type MouseEvent } from "react"

import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { cn } from "@/lib/utils"

import { candidateColumns } from "./columns"
import type { Application, ApplicationSort } from "./model"
import { nextSort, sortToSorting, type SortableColumn } from "./url-state"

/** The DOM id of a row, for focusing and scrolling it into view. */
export function rowDomId(applicationId: string): string {
  return `candidate-row-${applicationId}`
}

export type CandidatesTableProps = {
  rows: Application[]
  /** The campaign's required skills, lower-cased. */
  requiredSkills: ReadonlySet<string>
  sort: ApplicationSort
  onSortChange: (sort: ApplicationSort) => void
  rowSelection: RowSelectionState
  onRowSelectionChange: OnChangeFn<RowSelectionState>
  /** The keyboard-active row (J/K), also the one whose panel is open. */
  activeId: string | null
  onActiveChange: (applicationId: string) => void
  /** Open the row's panel (click or Enter). */
  onOpen: (applicationId: string) => void
  /** A newer page is loading behind the current rows. */
  isFetching?: boolean
}

const SORT_LABEL: Record<SortableColumn, string> = { name: "name", score: "score", updated: "last update" }

/**
 * The ranked candidates table (spec §5.1), presentational: rows, sort and
 * selection come in as props and changes go out as callbacks, so the same
 * component renders the API's page, the dev fixtures and the 500-row benchmark.
 *
 * Rows are memoised: moving the active row (J/K) or selecting one re-renders
 * those rows only, not the page (EPIC-01 §8, measured in the PR).
 */
export function CandidatesTable({
  rows,
  requiredSkills,
  sort,
  onSortChange,
  rowSelection,
  onRowSelectionChange,
  activeId,
  onActiveChange,
  onOpen,
  isFetching = false,
}: CandidatesTableProps) {
  const sorting = useMemo(() => [sortToSorting(sort)], [sort])
  const meta = useMemo(() => ({ requiredSkills }), [requiredSkills])
  // TanStack Table v8 is not React Compiler-safe; this component memoises by hand (memoised rows).
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data: rows,
    columns: candidateColumns,
    getRowId: (application) => application.id,
    getCoreRowModel: getCoreRowModel(),
    state: { sorting, rowSelection },
    onRowSelectionChange,
    enableRowSelection: true,
    manualSorting: true,
    manualPagination: true,
    meta,
  })

  const tableRows = table.getRowModel().rows
  const focusableId = activeId !== null && rows.some((row) => row.id === activeId) ? activeId : (rows[0]?.id ?? null)

  return (
    <Table
      data-slot="candidates-table"
      aria-label="Candidates"
      aria-busy={isFetching || undefined}
      className={cn("text-dense transition-opacity", isFetching && "opacity-70")}
    >
      <TableHeader className="sticky top-0 z-10 bg-background">
        {table.getHeaderGroups().map((group) => (
          <TableRow key={group.id} className="hover:bg-transparent">
            {group.headers.map((header) => {
              const { className, sortColumn, label } = header.column.columnDef.meta ?? {}
              const sorted = header.column.getIsSorted()
              return (
                <TableHead
                  key={header.id}
                  className={cn("h-9 text-xs text-muted-foreground", className)}
                  aria-sort={sortColumn ? (sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : "none") : undefined}
                  aria-label={label}
                >
                  {sortColumn ? (
                    <button
                      type="button"
                      className="-mx-1 inline-flex items-center gap-1 rounded-sm px-1 py-0.5 font-medium hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                      onClick={() => onSortChange(nextSort(sortColumn, sort))}
                      aria-label={`Sort by ${SORT_LABEL[sortColumn]}`}
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      {sorted === "desc" ? (
                        <ArrowDownIcon className="size-3.5" aria-hidden="true" />
                      ) : sorted === "asc" ? (
                        <ArrowUpIcon className="size-3.5" aria-hidden="true" />
                      ) : (
                        <ChevronsUpDownIcon className="size-3.5 opacity-50" aria-hidden="true" />
                      )}
                    </button>
                  ) : (
                    flexRender(header.column.columnDef.header, header.getContext())
                  )}
                </TableHead>
              )
            })}
          </TableRow>
        ))}
      </TableHeader>
      <TableBody>
        {tableRows.map((row) => (
          <CandidateRow
            key={row.id}
            row={row}
            selected={rowSelection[row.id] === true}
            active={row.id === activeId}
            focusable={row.id === focusableId}
            requiredSkills={requiredSkills}
            onActiveChange={onActiveChange}
            onOpen={onOpen}
          />
        ))}
      </TableBody>
    </Table>
  )
}

type CandidateRowProps = {
  row: Row<Application>
  selected: boolean
  active: boolean
  /** The row Tab lands on (roving tabindex): the active row, else the first. */
  focusable: boolean
  /** Passed only so a change re-renders the memoised row (cells read it from the table's meta). */
  requiredSkills: ReadonlySet<string>
  onActiveChange: (applicationId: string) => void
  onOpen: (applicationId: string) => void
}

/** Clicks on these do their own thing and do not open the panel. */
const INTERACTIVE = "button, a, input, label, [role='checkbox'], [data-column='select']"

const CandidateRow = memo(function CandidateRow({
  row,
  selected,
  active,
  focusable,
  requiredSkills,
  onActiveChange,
  onOpen,
}: CandidateRowProps) {
  const id = row.id
  const handleClick = (event: MouseEvent<HTMLTableRowElement>) => {
    if ((event.target as Element).closest(INTERACTIVE)) return
    onActiveChange(id)
    onOpen(id)
  }
  const handleKeyDown = (event: KeyboardEvent<HTMLTableRowElement>) => {
    if (event.target !== event.currentTarget) return
    if (event.key === "Enter") {
      event.preventDefault()
      onOpen(id)
    } else if (event.key === " ") {
      event.preventDefault()
      row.toggleSelected(!selected)
    }
  }
  return (
    // A plain <tr> rather than TableRow: no colour transition or :has() selector on 500 rows
    // (both showed up as style-recalc time when selecting all, see the PR's measurements).
    <tr
      id={rowDomId(id)}
      data-row-id={id}
      data-state={selected ? "selected" : undefined}
      data-active={active || undefined}
      aria-selected={selected}
      aria-current={active || undefined}
      tabIndex={focusable ? 0 : -1}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      onFocus={(event) => {
        if (event.target === event.currentTarget && !active) onActiveChange(id)
      }}
      data-slot="table-row"
      className="cursor-pointer border-b outline-none hover:bg-muted/50 data-[state=selected]:bg-muted focus-visible:bg-muted/50 focus-visible:shadow-[inset_2px_0_0_var(--ring)] data-active:bg-muted/60 data-active:shadow-[inset_2px_0_0_var(--primary)]"
    >
      {row.getVisibleCells().map((cell) =>
        cell.column.id === SELECT_COLUMN ? (
          <TableCell key={cell.id} data-column={cell.column.id} className={cn("py-1.5", cell.column.columnDef.meta?.className)}>
            {flexRender(cell.column.columnDef.cell, cell.getContext())}
          </TableCell>
        ) : null,
      )}
      <DataCells row={row} requiredSkills={requiredSkills} />
    </tr>
  )
})

const SELECT_COLUMN = "select"

/**
 * Every cell but the checkbox. Memoised on the application (and the required
 * skills), so selecting or activating a row re-renders its checkbox and
 * attributes only, not its score bar, badges, skills and dates.
 */
const DataCells = memo(function DataCells({
  row,
}: {
  row: Row<Application>
  requiredSkills: ReadonlySet<string>
}) {
  return row
    .getVisibleCells()
    .filter((cell) => cell.column.id !== SELECT_COLUMN)
    .map((cell) => (
      <TableCell key={cell.id} data-column={cell.column.id} className={cn("py-1.5", cell.column.columnDef.meta?.className)}>
        {flexRender(cell.column.columnDef.cell, cell.getContext())}
      </TableCell>
    ))
})

/** Skeleton rows while the first page loads (spec §5.1: designed, not blank). */
export function CandidatesTableSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <Table aria-label="Candidates" aria-busy="true" className="text-dense">
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          {["w-8", "min-w-48", "w-32", "w-36", "min-w-56", "w-24", "w-28", "w-32"].map((width, index) => (
            <TableHead key={index} className={cn("h-9", width)}>
              <Skeleton className="h-3 w-12" />
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {Array.from({ length: rows }, (_, index) => (
          <TableRow key={index} data-slot="skeleton-row" className="hover:bg-transparent">
            <TableCell className="py-2.5">
              <Skeleton className="size-4" />
            </TableCell>
            <TableCell>
              <Skeleton className="mb-1 h-3.5 w-32" />
              <Skeleton className="h-3 w-40" />
            </TableCell>
            <TableCell>
              <Skeleton className="h-3 w-20" />
            </TableCell>
            <TableCell>
              <Skeleton className="h-4 w-24 rounded-full" />
            </TableCell>
            <TableCell>
              <Skeleton className="h-4 w-40" />
            </TableCell>
            <TableCell>
              <Skeleton className="ml-auto h-3 w-10" />
            </TableCell>
            <TableCell>
              <Skeleton className="h-3 w-16" />
            </TableCell>
            <TableCell>
              <Skeleton className="h-3 w-16" />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
