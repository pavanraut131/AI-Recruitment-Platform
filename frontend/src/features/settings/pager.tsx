import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react"

import { Button } from "@/components/ui/button"

/** "21–40 of 45 clients" with Previous and Next, when there is more than one page. */
export function Pager({
  label,
  page,
  pageSize,
  total,
  shown,
  noun,
  onPage,
}: {
  label: string
  page: number
  pageSize: number
  total: number
  shown: number
  noun: string
  onPage: (page: number) => void
}) {
  if (total <= pageSize && page === 1) return null
  const first = (page - 1) * pageSize + 1
  const last = first + Math.max(shown, 1) - 1
  return (
    <div className="flex items-center justify-between gap-4 text-dense text-muted-foreground">
      <p aria-live="polite" className="tabular-nums">
        {shown === 0 ? `Nothing on page ${page}` : `${first}–${last} of ${total} ${noun}s`}
      </p>
      <nav aria-label={label} className="flex items-center gap-1">
        <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          <ChevronLeftIcon data-icon="inline-start" />
          Previous
        </Button>
        <Button variant="outline" size="sm" disabled={last >= total} onClick={() => onPage(page + 1)}>
          Next
          <ChevronRightIcon data-icon="inline-end" />
        </Button>
      </nav>
    </div>
  )
}
