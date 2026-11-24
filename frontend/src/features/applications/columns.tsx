import { createColumnHelper, type RowData } from "@tanstack/react-table"
import { format, formatDistanceToNowStrict } from "date-fns"

import { MustHavesCell, RowCheckbox, ScoreCell, StageCell } from "./cells"
import { formatYears, personName, type Application } from "./model"
import type { SortableColumn } from "./url-state"

declare module "@tanstack/react-table" {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- the generics must match TanStack's declaration
  interface TableMeta<TData extends RowData> {
    /** The campaign's required skills, lower-cased (the must-haves column). */
    requiredSkills: ReadonlySet<string>
  }
  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- the generics must match TanStack's declaration
  interface ColumnMeta<TData extends RowData, TValue> {
    /** Header and cell classes (widths, alignment). */
    className?: string
    /** The column the server sorts by when its header is clicked. */
    sortColumn?: SortableColumn
    /** The header's accessible name when it shows no text (the select column). */
    label?: string
  }
}

const column = createColumnHelper<Application>()

/**
 * The candidates table's columns (spec §5.1, docs/design/README.md): the
 * score reads as number, meter and label in one column; must-haves are
 * counted against the campaign's required skills. Sorting is server-side: the
 * sortable headers map to the API's `sort` values (`url-state.ts`), so
 * TanStack only renders the sorting state it is given.
 */
export const candidateColumns = [
  column.display({
    id: "select",
    meta: { className: "w-9 pl-3", label: "Select" },
    header: ({ table }) => (
      <RowCheckbox
        aria-label="Select all candidates on this page"
        checked={table.getIsAllRowsSelected()}
        indeterminate={table.getIsSomeRowsSelected()}
        onCheckedChange={(checked) => table.toggleAllRowsSelected(checked)}
      />
    ),
    cell: ({ row }) => (
      <RowCheckbox
        aria-label={`Select ${personName(row.original)}`}
        checked={row.getIsSelected()}
        onCheckedChange={(checked) => row.toggleSelected(checked)}
      />
    ),
  }),
  column.accessor((application) => personName(application), {
    id: "name",
    header: "Candidate",
    meta: { className: "min-w-52", sortColumn: "name" },
    cell: ({ row }) => (
      <div className="flex min-w-0 flex-col">
        <span className="truncate text-sm font-semibold">{personName(row.original)}</span>
        <span className="truncate text-muted-foreground">{row.original.person.email ?? "No email"}</span>
      </div>
    ),
  }),
  column.accessor("score", {
    header: "Score",
    meta: { className: "w-56", sortColumn: "score" },
    cell: ({ row }) => <ScoreCell score={row.original.score} category={row.original.category} withLabel />,
  }),
  column.accessor("skills", {
    id: "musts",
    header: "Must-haves",
    meta: { className: "w-28" },
    cell: ({ getValue, table }) => (
      <MustHavesCell skills={getValue()} required={table.options.meta?.requiredSkills ?? new Set()} />
    ),
  }),
  column.accessor("years_of_experience", {
    id: "experience",
    header: "Experience",
    meta: { className: "w-24" },
    cell: ({ getValue }) => <span className="text-ink-2">{formatYears(getValue())}</span>,
  }),
  column.accessor("pipeline_stage", {
    id: "stage",
    header: "Stage",
    meta: { className: "w-32" },
    cell: ({ getValue }) => <StageCell stage={getValue()} />,
  }),
  column.accessor("updated_at", {
    id: "updated",
    header: "Updated",
    meta: { className: "w-28 pr-3 text-right", sortColumn: "updated" },
    cell: ({ getValue }) => {
      const date = new Date(getValue())
      return (
        <time dateTime={getValue()} title={format(date, "PPpp")} className="text-muted-foreground">
          {formatDistanceToNowStrict(date, { addSuffix: true })}
        </time>
      )
    },
  }),
]
