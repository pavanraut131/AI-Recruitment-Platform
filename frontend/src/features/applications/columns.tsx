import { createColumnHelper, type RowData } from "@tanstack/react-table"
import { format, formatDistanceToNowStrict } from "date-fns"

import { Checkbox } from "@/components/ui/checkbox"

import { CategoryBadge, ScoreCell, SkillsCell, StageCell } from "./cells"
import { formatYears, personName, type Application } from "./model"
import type { SortableColumn } from "./url-state"

declare module "@tanstack/react-table" {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- the generics must match TanStack's declaration
  interface TableMeta<TData extends RowData> {
    /** The campaign's required skills, lower-cased (highlighted in the skills column). */
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
 * The candidates table's columns (spec §5.1). Sorting is server-side: the
 * sortable headers map to the API's `sort` values (`url-state.ts`), so TanStack
 * only renders the sorting state it is given.
 */
export const candidateColumns = [
  column.display({
    id: "select",
    meta: { className: "w-8", label: "Select" },
    header: ({ table }) => (
      <Checkbox
        aria-label="Select all candidates on this page"
        checked={table.getIsAllRowsSelected() ? true : table.getIsSomeRowsSelected() ? "indeterminate" : false}
        onCheckedChange={(checked) => table.toggleAllRowsSelected(checked === true)}
      />
    ),
    cell: ({ row }) => (
      <Checkbox
        aria-label={`Select ${personName(row.original)}`}
        checked={row.getIsSelected()}
        onCheckedChange={(checked) => row.toggleSelected(checked === true)}
      />
    ),
  }),
  column.accessor((application) => personName(application), {
    id: "name",
    header: "Candidate",
    meta: { className: "min-w-48", sortColumn: "name" },
    cell: ({ row }) => (
      <div className="flex min-w-0 flex-col">
        <span className="truncate font-medium">{personName(row.original)}</span>
        <span className="truncate text-xs text-muted-foreground">{row.original.person.email ?? "No email"}</span>
      </div>
    ),
  }),
  column.accessor("score", {
    header: "Score",
    meta: { className: "w-32", sortColumn: "score" },
    cell: ({ row }) => <ScoreCell score={row.original.score} category={row.original.category} />,
  }),
  column.accessor("category", {
    header: "Category",
    meta: { className: "w-36" },
    cell: ({ getValue }) => <CategoryBadge category={getValue()} />,
  }),
  column.accessor("skills", {
    header: "Top skills",
    meta: { className: "min-w-56" },
    cell: ({ getValue, table }) => <SkillsCell skills={getValue()} required={table.options.meta?.requiredSkills ?? new Set()} />,
  }),
  column.accessor("years_of_experience", {
    id: "experience",
    header: "Experience",
    meta: { className: "w-24 text-right" },
    cell: ({ getValue }) => <span className="tabular-nums">{formatYears(getValue())}</span>,
  }),
  column.accessor("pipeline_stage", {
    id: "stage",
    header: "Stage",
    meta: { className: "w-28" },
    cell: ({ getValue }) => <StageCell stage={getValue()} />,
  }),
  column.accessor("updated_at", {
    id: "updated",
    header: "Updated",
    meta: { className: "w-32", sortColumn: "updated" },
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
