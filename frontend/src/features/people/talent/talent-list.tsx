"use client"

import { formatDistanceToNow } from "date-fns"
import { ChevronLeftIcon, ChevronRightIcon, SearchIcon, SearchXIcon, UsersIcon } from "lucide-react"
import Link from "next/link"
import { useRouter } from "next/navigation"

import { EmptyState, ErrorState } from "@/components/data-states"
import { Button } from "@/components/ui/button"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { displayName } from "@/lib/applications"
import { personHref } from "@/lib/routes"
import { cn } from "@/lib/utils"

import { usePeople, type PersonListItem } from "./queries"
import { useTalentParams } from "./use-talent-params"

/** Skills shown per row; the rest are counted ("+4"). */
const SKILLS_SHOWN = 3

/**
 * `/talent` (spec §4): everyone who has applied to any campaign, as people,
 * most recently updated first. Search on name or email; the search and the
 * page are in the URL. (E6 replaces the search with skill and semantic search.)
 */
export function TalentList() {
  const params = useTalentParams()
  const query = usePeople(params.apiParams)

  return (
    <div className="mx-auto flex w-full max-w-[1280px] flex-col gap-6 px-4 pt-6 pb-14 sm:px-8 lg:px-14">
      <header className="flex flex-col gap-1.5">
        <h1 className="text-title font-semibold tracking-tight">Talent</h1>
        <p className="text-ink-2">Everyone who has applied to any campaign, one row per person.</p>
      </header>

      <InputGroup className="w-full max-w-xs">
        <InputGroupAddon>
          <SearchIcon aria-hidden="true" />
        </InputGroupAddon>
        <InputGroupInput
          type="search"
          aria-label="Search people by name or email"
          placeholder="Name or email"
          value={params.q}
          onChange={(event) => params.setSearch(event.target.value)}
          maxLength={200}
        />
      </InputGroup>

      <Results params={params} query={query} />
    </div>
  )
}

type TalentParams = ReturnType<typeof useTalentParams>

function Results({ params, query }: { params: TalentParams; query: ReturnType<typeof usePeople> }) {
  if (query.isPending) return <TalentTableSkeleton />

  if (query.isError) {
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} retrying={query.isFetching} />
  }

  const { items, total, page, page_size: pageSize } = query.data

  if (items.length === 0) {
    if (params.page > 1 && total > 0) {
      return (
        <EmptyState
          icon={SearchXIcon}
          title="No people on this page"
          description={`There are ${total} matching people, on fewer pages.`}
          action={
            <Button variant="outline" onClick={() => params.setPage(1)}>
              Go to the first page
            </Button>
          }
        />
      )
    }
    if (params.hasSearch) {
      return (
        <EmptyState
          icon={SearchXIcon}
          title="No one matches"
          description="Search matches a name or an email address. Check the spelling, or try part of it."
          action={
            <Button variant="outline" onClick={params.clearSearch}>
              Clear search
            </Button>
          }
        />
      )
    }
    return (
      <EmptyState
        icon={UsersIcon}
        title="No people yet"
        description="People show up here as their resumes are processed, once per person across every campaign."
        action={
          <Button asChild variant="outline">
            <Link href="/campaigns">Go to campaigns</Link>
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
        className={cn("overflow-x-auto border-t transition-opacity motion-reduce:transition-none", updating && "opacity-60")}
      >
        <TalentTable people={items} />
      </div>
      <div className="flex items-center justify-between gap-4 text-dense text-muted-foreground">
        <p aria-live="polite">
          {total === 1 ? "1 person" : `${first === last ? first : `${first}–${last}`} of ${total} people`}
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

function TalentTable({ people }: { people: PersonListItem[] }) {
  const router = useRouter()
  return (
    <Table className="min-w-[720px] text-dense">
      <TableCaption className="sr-only">People, most recently updated first. Open a profile by its name.</TableCaption>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead className="pl-1">Name</TableHead>
          <TableHead>Latest role</TableHead>
          <TableHead>Skills</TableHead>
          <TableHead className={NUMERIC}>Applications</TableHead>
          <TableHead className="pr-1 text-right">Last seen</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {people.map((person) => {
          const href = personHref(person.id)
          return (
            <TableRow
              key={person.id}
              // The name link is the keyboard and screen-reader target; the
              // whole row is a larger click target for the mouse.
              onClick={(event) => {
                if ((event.target as HTMLElement).closest("a, button")) return
                router.push(href)
              }}
              className="cursor-pointer"
            >
              <TableCell className="max-w-64 pl-1">
                <Link
                  href={href}
                  className="block truncate rounded-sm text-sm font-semibold outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {displayName(person.name)}
                </Link>
                {person.email && <span className="block truncate text-xs text-muted-foreground">{person.email}</span>}
              </TableCell>
              <TableCell className="max-w-56 truncate text-ink-2">{person.latest_role ?? "—"}</TableCell>
              <TableCell>
                <Skills skills={person.skills} />
              </TableCell>
              <TableCell className={NUMERIC}>{person.application_count}</TableCell>
              <TableCell className="pr-1 text-right whitespace-nowrap text-muted-foreground">
                {person.last_applied_at ? <RelativeTime iso={person.last_applied_at} /> : "—"}
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

function Skills({ skills }: { skills: string[] }) {
  if (skills.length === 0) return <span className="text-muted-foreground">—</span>
  const shown = skills.slice(0, SKILLS_SHOWN)
  const more = skills.length - shown.length
  return (
    <ul className="flex flex-wrap items-center gap-1" aria-label="Skills">
      {shown.map((skill) => (
        <li key={skill} className="inline-flex h-6 items-center rounded-full border border-input px-2 text-xs text-ink-2">
          {skill}
        </li>
      ))}
      {more > 0 && (
        <li className="text-xs text-muted-foreground" title={skills.slice(SKILLS_SHOWN).join(", ")}>
          +{more}
          <span className="sr-only"> more: {skills.slice(SKILLS_SHOWN).join(", ")}</span>
        </li>
      )}
    </ul>
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

function TalentTableSkeleton() {
  return (
    <div className="border-t" aria-busy="true" aria-label="Loading people">
      <div className="flex h-9 items-center gap-6 border-b px-1">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-3 w-20" />
        <Skeleton className="ml-auto h-3 w-40" />
      </div>
      {Array.from({ length: 8 }, (_, index) => (
        <div key={index} className="flex h-[58px] items-center gap-6 border-b px-1">
          <Skeleton className="h-3.5 w-40" />
          <Skeleton className="h-3.5 w-32" />
          <Skeleton className="h-5 w-36 rounded-full" />
          <Skeleton className="ml-auto h-3.5 w-8" />
          <Skeleton className="h-3.5 w-20" />
        </div>
      ))}
    </div>
  )
}
