"use client"

import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"

import { PAGE_SIZES, type PageSize } from "./model"

export function pageCount(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil(total / pageSize))
}

/** "51–100 of 312": the rows shown on this page. */
export function rangeLabel(page: number, pageSize: number, shown: number, total: number): string {
  if (total === 0 || shown === 0) return `0 of ${total}`
  const first = (page - 1) * pageSize + 1
  return `${first}–${first + shown - 1} of ${total}`
}

export function CandidatesPagination({
  page,
  pageSize,
  shown,
  total,
  onPageChange,
  onPageSizeChange,
}: {
  page: number
  pageSize: PageSize
  shown: number
  total: number
  onPageChange: (page: number) => void
  onPageSizeChange: (size: PageSize) => void
}) {
  const pages = pageCount(total, pageSize)
  return (
    <nav aria-label="Pagination" className="flex items-center justify-between gap-4 text-sm">
      <p className="text-muted-foreground tabular-nums" aria-live="polite">
        {rangeLabel(page, pageSize, shown, total)}
      </p>
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground" id="candidates-page-size">
            Rows per page
          </span>
          <Select value={String(pageSize)} onValueChange={(value) => onPageSizeChange(Number(value) as PageSize)}>
            <SelectTrigger size="sm" className="w-18" aria-labelledby="candidates-page-size">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PAGE_SIZES.map((size) => (
                <SelectItem key={size} value={String(size)}>
                  {size}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <span className="text-muted-foreground tabular-nums">
          Page {Math.min(page, pages)} of {pages}
        </span>
        <div className="flex items-center gap-1">
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="Previous page"
            disabled={page <= 1}
            onClick={() => onPageChange(page - 1)}
          >
            <ChevronLeftIcon aria-hidden="true" />
          </Button>
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="Next page"
            disabled={page >= pages}
            onClick={() => onPageChange(page + 1)}
          >
            <ChevronRightIcon aria-hidden="true" />
          </Button>
        </div>
      </div>
    </nav>
  )
}
