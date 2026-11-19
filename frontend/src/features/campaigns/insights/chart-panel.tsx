"use client"

import * as React from "react"
import { TableIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { cn } from "@/lib/utils"

export type DataTable = {
  columns: { header: string; numeric?: boolean }[]
  rows: { key: string; cells: React.ReactNode[] }[]
}

/**
 * One Insights chart with its text alternatives (spec §8, WCAG 1.1.1):
 *
 * - a visible one-sentence **summary** of what the chart shows;
 * - the chart's **data as a table**, always in the accessibility tree
 *   (visually hidden) and shown in place of the chart while the Table toggle
 *   is pressed.
 *
 * The chart itself is decorative to assistive technology (`aria-hidden`):
 * the summary and the table carry the same numbers.
 */
export function ChartPanel({
  title,
  summary,
  table,
  empty,
  footer,
  className,
  children,
}: {
  title: string
  summary: React.ReactNode
  table: DataTable
  /** Shown instead of the chart and table when there is nothing to plot. */
  empty?: React.ReactNode
  footer?: React.ReactNode
  className?: string
  children: React.ReactNode
}) {
  const [showTable, setShowTable] = React.useState(false)
  const id = React.useId()
  const titleId = `${id}-title`
  const tableId = `${id}-table`

  return (
    <section aria-labelledby={titleId} className={cn("flex flex-col gap-3 rounded-lg border p-4", className)}>
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <h2 id={titleId} className="text-sm font-medium">
            {title}
          </h2>
          <p className="text-sm text-muted-foreground">{summary}</p>
        </div>
        {!empty && (
          // A toggle with a fixed name ("Table for …"); aria-pressed says whether the table is shown.
          <Button
            type="button"
            variant={showTable ? "secondary" : "ghost"}
            size="sm"
            aria-pressed={showTable}
            aria-controls={tableId}
            onClick={() => setShowTable((value) => !value)}
            className={cn("shrink-0", !showTable && "text-muted-foreground")}
          >
            <TableIcon data-icon="inline-start" />
            Table{" "}
            <span className="sr-only">for {title.toLowerCase()}</span>
          </Button>
        )}
      </header>

      {empty ? (
        <div className="flex min-h-40 flex-1 items-center justify-center rounded-md bg-muted/40 px-4 text-center text-sm text-muted-foreground">
          {empty}
        </div>
      ) : (
        <>
          {/* Unmounted, not hidden, while the table shows: a hidden chart measures 0 × 0. */}
          {!showTable && (
            <div aria-hidden="true" data-testid="chart">
              {children}
            </div>
          )}
          {/*
            Visually hidden, the table's own scroll container (overflow-x-auto)
            would be a 1 px wide scrollable region with nothing to focus, which
            keyboard users can't scroll (axe: scrollable-region-focusable).
          */}
          <div
            id={tableId}
            className={cn(!showTable && "sr-only [&_[data-slot=table-container]]:overflow-visible")}
          >
            <Table className="text-dense">
              <TableCaption className="sr-only">{title}</TableCaption>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  {table.columns.map((column) => (
                    <TableHead key={column.header} className={cn(column.numeric && "text-right")}>
                      {column.header}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {table.rows.map((row) => (
                  <TableRow key={row.key}>
                    {row.cells.map((cell, index) => (
                      <TableCell
                        key={table.columns[index]?.header ?? index}
                        className={cn(table.columns[index]?.numeric && "text-right tabular-nums")}
                      >
                        {cell}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </>
      )}
      {footer}
    </section>
  )
}
