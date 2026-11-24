"use client"

import Link from "next/link"
import { useRouter } from "next/navigation"
import { formatDistanceToNow } from "date-fns"
import { BriefcaseIcon, ChevronLeftIcon, ChevronRightIcon, Loader2Icon, PlusIcon, SearchIcon, SearchXIcon } from "lucide-react"

import { CampaignStatusBadge } from "@/components/campaign/status-badge"
import { Button } from "@/components/ui/button"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { cn } from "@/lib/utils"

import { useCampaigns, type Campaign } from "../api"
import { EmptyState, ErrorState } from "@/components/data-states"
import { CAMPAIGN_STATUS_LABELS, CAMPAIGN_STATUSES } from "../schema"
import { useCampaignListParams } from "./use-campaign-list-params"

/** `/campaigns`: every campaign, filtered by status and searched by title or role, all in the URL. */
export function CampaignList() {
  const params = useCampaignListParams()
  const query = useCampaigns(params.apiParams)

  return (
    <div className="mx-auto flex w-full max-w-[1080px] flex-col gap-6 px-4 pt-6 pb-14 sm:px-8 lg:px-14">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <h1 className="text-title font-semibold tracking-tight">Campaigns</h1>
          <p className="text-ink-2">One campaign per opening, with its resumes, ranked candidates and pipeline.</p>
        </div>
        <Button asChild size="lg">
          <Link href="/campaigns/new">
            <PlusIcon data-icon="inline-start" />
            New campaign
          </Link>
        </Button>
      </header>

      <Toolbar params={params} />

      <Results params={params} query={query} />
    </div>
  )
}

type ListParams = ReturnType<typeof useCampaignListParams>

function Toolbar({ params }: { params: ListParams }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <InputGroup className="w-full max-w-xs">
        <InputGroupAddon>
          <SearchIcon aria-hidden="true" />
        </InputGroupAddon>
        <InputGroupInput
          type="search"
          aria-label="Search campaigns by title or role"
          placeholder="Title or role"
          value={params.q}
          onChange={(event) => params.setSearch(event.target.value)}
          maxLength={200}
        />
      </InputGroup>
      <div role="group" aria-label="Filter by status" className="flex items-center gap-1">
        {CAMPAIGN_STATUSES.map((status) => {
          const pressed = params.status.includes(status)
          return (
            <Button
              key={status}
              type="button"
              size="sm"
              variant="ghost"
              aria-pressed={pressed}
              onClick={() => params.toggleStatus(status)}
              className={cn(
                "rounded-full border border-transparent",
                pressed ? "border-input bg-muted text-foreground" : "text-muted-foreground",
              )}
            >
              {CAMPAIGN_STATUS_LABELS[status]}
            </Button>
          )
        })}
      </div>
      {params.hasFilters && (
        <Button type="button" size="sm" variant="link" onClick={params.clearFilters} className="px-1">
          Clear filters
        </Button>
      )}
    </div>
  )
}

function Results({ params, query }: { params: ListParams; query: ReturnType<typeof useCampaigns> }) {
  if (query.isPending) return <CampaignTableSkeleton />

  if (query.isError) {
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} retrying={query.isFetching} />
  }

  const { items, total, page, page_size: pageSize } = query.data

  if (items.length === 0) {
    if (params.page > 1 && total > 0) {
      return (
        <EmptyState
          icon={SearchXIcon}
          title="No campaigns on this page"
          description={`There are ${total} matching campaigns, on fewer pages.`}
          action={
            <Button variant="outline" onClick={() => params.setPage(1)}>
              Go to the first page
            </Button>
          }
        />
      )
    }
    if (params.hasFilters) {
      return (
        <EmptyState
          icon={SearchXIcon}
          title="No campaigns match"
          description="Try part of the title or role, or another status."
          action={
            <Button variant="outline" onClick={params.clearFilters}>
              Clear filters
            </Button>
          }
        />
      )
    }
    return (
      <EmptyState
        icon={BriefcaseIcon}
        title="No campaigns yet"
        description="Create a campaign for an opening, then upload resumes to get a ranked shortlist."
        action={
          <Button asChild size="lg">
            <Link href="/campaigns/new">
              <PlusIcon data-icon="inline-start" />
              New campaign
            </Link>
          </Button>
        }
      />
    )
  }

  const first = (page - 1) * pageSize + 1
  const last = first + items.length - 1
  const updating = query.isPlaceholderData

  return (
    <div className="flex flex-col gap-3">
      <div
        aria-busy={updating}
        className={cn("border-t transition-opacity motion-reduce:transition-none", updating && "opacity-60")}
      >
        <CampaignTable campaigns={items} />
      </div>
      <div className="flex items-center justify-between gap-4 text-dense text-muted-foreground">
        <p aria-live="polite" className="tabular-nums">
          {total === 1 ? "1 campaign" : `${first === last ? first : `${first}–${last}`} of ${total} campaigns`}
        </p>
        {total > pageSize && (
          <nav aria-label="Pagination" className="flex items-center gap-1">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => params.setPage(page - 1)}>
              <ChevronLeftIcon data-icon="inline-start" />
              Previous
            </Button>
            <Button variant="outline" size="sm" disabled={last >= total} onClick={() => params.setPage(page + 1)}>
              Next
              <ChevronRightIcon data-icon="inline-end" />
            </Button>
          </nav>
        )}
      </div>
    </div>
  )
}

const NUMERIC = "text-right tabular-nums"

function CampaignTable({ campaigns }: { campaigns: Campaign[] }) {
  const router = useRouter()
  return (
    <Table className="min-w-[720px] text-dense">
      <TableCaption className="sr-only">Campaigns, newest first. Open a campaign by its title.</TableCaption>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead className="pl-1">Campaign</TableHead>
          <TableHead>Status</TableHead>
          <TableHead className={NUMERIC}>Applicants</TableHead>
          <TableHead className={NUMERIC}>Strong matches</TableHead>
          <TableHead className={NUMERIC}>Processing</TableHead>
          <TableHead className="pr-1 text-right">Last activity</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {campaigns.map((campaign) => {
          const href = `/campaigns/${campaign.id}`
          return (
            <TableRow
              key={campaign.id}
              // The title link is the keyboard and screen-reader target; the
              // whole row is a larger click target for the mouse.
              onClick={(event) => {
                if ((event.target as HTMLElement).closest("a, button")) return
                router.push(href)
              }}
              className="cursor-pointer"
            >
              <TableCell className="max-w-80 pl-1">
                <Link
                  href={href}
                  className="block truncate rounded-sm text-sm font-semibold outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {campaign.title}
                </Link>
                <span className="block truncate text-muted-foreground">{campaign.role}</span>
              </TableCell>
              <TableCell>
                <CampaignStatusBadge status={campaign.status} />
              </TableCell>
              <TableCell className={NUMERIC}>{campaign.counts.applications}</TableCell>
              <TableCell className={NUMERIC}>
                {campaign.counts.strong_matches > 0 ? (
                  <span className="font-semibold text-category-strong-foreground">{campaign.counts.strong_matches}</span>
                ) : (
                  <span className="text-muted-foreground">0</span>
                )}
              </TableCell>
              <TableCell className={NUMERIC}>
                <ProcessingCount inProgress={campaign.counts.in_progress} failed={campaign.counts.failed} />
              </TableCell>
              <TableCell className="pr-1 text-right whitespace-nowrap text-muted-foreground">
                <RelativeTime iso={campaign.last_activity_at} />
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

function ProcessingCount({ inProgress, failed }: { inProgress: number; failed: number }) {
  if (inProgress === 0 && failed === 0) {
    return (
      <span className="text-muted-foreground">
        <span aria-hidden="true">—</span>
        <span className="sr-only">None</span>
      </span>
    )
  }
  return (
    <span className="inline-flex items-center justify-end gap-2">
      {inProgress > 0 && (
        <span className="inline-flex items-center gap-1">
          <Loader2Icon className="size-3.5 animate-spin text-primary motion-reduce:animate-none" aria-hidden="true" />
          {inProgress}
          <span className="sr-only">in progress</span>
        </span>
      )}
      {failed > 0 && <span className="text-destructive">{failed} failed</span>}
    </span>
  )
}

function RelativeTime({ iso }: { iso: string }) {
  const date = new Date(iso)
  return (
    <time dateTime={iso} title={date.toLocaleString()}>
      {formatDistanceToNow(date, { addSuffix: true })}
    </time>
  )
}

function CampaignTableSkeleton() {
  return (
    <div className="border-t" aria-busy="true" aria-label="Loading campaigns">
      <div className="flex h-9 items-center gap-6 border-b px-1">
        <Skeleton className="h-3 w-20" />
        <Skeleton className="h-3 w-14" />
        <Skeleton className="ml-auto h-3 w-40" />
      </div>
      {Array.from({ length: 6 }, (_, index) => (
        <div key={index} className="flex h-[58px] items-center gap-6 border-b px-1">
          <div className="flex flex-col gap-1.5">
            <Skeleton className="h-4 w-56" />
            <Skeleton className="h-3 w-32" />
          </div>
          <Skeleton className="h-3.5 w-14" />
          <Skeleton className="ml-auto h-3.5 w-10" />
          <Skeleton className="h-3.5 w-10" />
          <Skeleton className="h-3.5 w-20" />
        </div>
      ))}
    </div>
  )
}
