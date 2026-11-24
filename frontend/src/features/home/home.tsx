"use client"

import { formatDistanceToNowStrict } from "date-fns"
import { BriefcaseIcon, LoaderCircleIcon, PlusIcon } from "lucide-react"
import Link from "next/link"
import { useSyncExternalStore, type CSSProperties, type ReactNode } from "react"

import { ScoreCell, categoryBarClass } from "@/components/application/score"
import { EmptyState, ErrorState } from "@/components/data-states"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import type { Schema } from "@/lib/api/client"
import { displayName } from "@/lib/applications"
import { applicationHref, campaignHref } from "@/lib/routes"
import { cn } from "@/lib/utils"

import { useHome, useWorkspaceStats, type Home as HomeData, type ProcessingResume, type RecentMatch } from "./queries"
import { homeFacts, longDate, plural, type HomeFact } from "./summary"

type Campaign = Schema<"Campaign">

/** A processing resume's status, in the Uploads tab's words. */
const RESUME_STATUS_LABELS: Record<Schema<"ResumeStatus">, string> = {
  pending: "Queued",
  extracting: "Reading",
  processing: "Scoring",
  done: "Done",
  failed: "Failed",
  quota_exceeded: "Quota limit",
}

/*
 * The date follows the reader's clock, which the server does not know: the
 * server renders without it and the client fills it in right after hydration
 * (no mismatch), then keeps it current each minute.
 */
function subscribeToMinutes(onChange: () => void) {
  const timer = setInterval(onChange, 60_000)
  return () => clearInterval(timer)
}
function readClock(): string {
  const now = new Date()
  return longDate(now)
}
function useToday(): string | null {
  return useSyncExternalStore(subscribeToMinutes, readClock, () => null)
}

/**
 * `/` (spec §4), a calm "today" page that uses the window like the workspace:
 * its title and the day's numbers as one quiet row, then the focal point, the
 * strong matches nobody has looked
 * at yet (from `GET /home`, best first), the open campaigns as compact rows,
 * and a quiet line about uploads still processing. A first run (no campaigns
 * at all) invites creating one.
 */
export function Home() {
  const home = useHome()
  const stats = useWorkspaceStats()
  const today = useToday()

  const firstRun = stats.data?.campaigns.total === 0 && home.data !== undefined && isEmpty(home.data)
  const facts = home.data && stats.data ? homeFacts(home.data, stats.data) : null

  return (
    <div className="flex w-full flex-col gap-8 px-4 pt-3 pb-14 sm:px-6 lg:px-10 2xl:px-14">
      <header className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="flex min-w-0 flex-col gap-1.5">
          <h1 className="text-title font-semibold tracking-tight">Home</h1>
          <div className="min-h-6">
            {facts ? (
              <TodayFacts facts={facts} today={today} />
            ) : (stats.isPending || home.isPending) && !home.isError ? (
              <Skeleton className="mt-1 h-4 w-96 max-w-full" />
            ) : today ? (
              <p className="text-muted-foreground">{today}</p>
            ) : null}
          </div>
        </div>
        <NewCampaignButton />
      </header>

      {home.isPending || stats.isPending ? (
        <HomeSkeleton />
      ) : home.isError ? (
        <ErrorState error={home.error} onRetry={() => void home.refetch()} retrying={home.isFetching} />
      ) : firstRun ? (
        <EmptyState
          icon={BriefcaseIcon}
          title="Create your first campaign"
          description="A campaign is one opening: describe the role, upload resumes, and get a ranked shortlist of candidates."
          action={<NewCampaignButton />}
        />
      ) : (
        <div className="grid items-start gap-x-12 gap-y-10 xl:grid-cols-[minmax(0,1fr)_minmax(320px,400px)]">
          <ReadyForReview matches={home.data.recent_strong_matches} />
          <div className="flex min-w-0 flex-col gap-6">
            <OpenCampaigns campaigns={home.data.active_campaigns} />
            <Processing processing={home.data.processing} />
          </div>
        </div>
      )}
    </div>
  )
}

function isEmpty(home: HomeData): boolean {
  return home.active_campaigns.length === 0 && home.processing.total === 0 && home.recent_strong_matches.length === 0
}

/** The day's numbers as one quiet row (bold counts, muted words), then the date. */
function TodayFacts({ facts, today }: { facts: HomeFact[]; today: string | null }) {
  return (
    <ul aria-label="Today" className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-ink-2">
      {facts.map((fact) => (
        <li key={fact.key}>
          {fact.count !== null && (
            <>
              <span className="font-semibold text-foreground tabular-nums">{fact.count.toLocaleString("en-GB")}</span>{" "}
            </>
          )}
          {fact.label}
        </li>
      ))}
      {today && <li className="text-muted-foreground sm:border-l sm:pl-5">{today}</li>}
    </ul>
  )
}

function NewCampaignButton() {
  return (
    <Button asChild>
      <Link href="/campaigns/new">
        <PlusIcon data-icon="inline-start" />
        New campaign
      </Link>
    </Button>
  )
}

function SectionHeading({
  id,
  title,
  hint,
  action,
}: {
  id: string
  title: string
  hint?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <h2 id={id} className="text-section font-semibold">
        {title}
      </h2>
      {hint && <span className="text-dense text-muted-foreground">{hint}</span>}
      {action && <span className="ml-auto">{action}</span>}
    </div>
  )
}

function initials(name: string | null): string {
  const words = (name ?? "").trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return "?"
  return (words[0][0] + (words.length > 1 ? words[words.length - 1][0] : "")).toUpperCase()
}

/** Strong matches with no stage yet, best first: the screen's focal point. */
function ReadyForReview({ matches, className }: { matches: RecentMatch[]; className?: string }) {
  const waiting = matches
    .filter((match) => match.pipeline_stage === null)
    .sort((a, b) => (b.score ?? -1) - (a.score ?? -1))
  return (
    <section aria-labelledby="home-ready" className={cn("flex min-w-0 flex-col gap-3", className)}>
      <SectionHeading id="home-ready" title="Ready for review" hint={waiting.length > 0 ? "Strong matches nobody has looked at yet, best first" : undefined} />
      {waiting.length === 0 ? (
        <p className="border-t py-6 text-ink-2">
          {matches.length === 0
            ? "No strong matches yet. They show up here as resumes are scored."
            : "You have looked at every recent strong match. Other candidates wait in each campaign's Candidates tab."}
        </p>
      ) : (
        <ul className="border-t">
          {waiting.map((match) => (
            <QueueItem key={match.id} match={match} />
          ))}
        </ul>
      )}
    </section>
  )
}

function QueueItem({ match }: { match: RecentMatch }) {
  const name = displayName(match.person.name)
  return (
    // The name's link covers the row (its ::after), so the whole row is one target with one name.
    <li className="relative flex items-center gap-3.5 border-b px-1 py-3.5 transition-colors focus-within:bg-muted/60 hover:bg-muted/60 sm:gap-4">
      <span
        aria-hidden="true"
        className="hidden size-9 shrink-0 place-items-center rounded-full bg-muted text-dense font-semibold text-ink-2 sm:grid"
      >
        {initials(match.person.name)}
      </span>
      <div className="flex min-w-36 flex-1 flex-col gap-0.5">
        <Link
          href={applicationHref(match.campaign.id, match.id)}
          className="truncate font-semibold outline-none after:absolute after:inset-0 focus-visible:underline"
        >
          {name}
        </Link>
        <span className="truncate text-dense text-muted-foreground">
          <span className="md:hidden">{match.campaign.title} · </span>
          scored <RelativeTime iso={match.created_at} />
        </span>
      </div>
      <span className="hidden max-w-72 min-w-0 shrink basis-44 truncate text-dense text-muted-foreground md:block 2xl:basis-72" title={match.campaign.title}>
        {match.campaign.title}
      </span>
      <span className="shrink-0">
        <ScoreCell score={match.score} category={match.category} size="md" />
      </span>
      <span
        aria-hidden="true"
        className="hidden h-8 shrink-0 items-center rounded-md border border-input px-3 text-dense font-medium sm:inline-flex"
      >
        Review
      </span>
    </li>
  )
}

function OpenCampaigns({ campaigns }: { campaigns: Campaign[] }) {
  return (
    <section aria-labelledby="home-campaigns" className="flex min-w-0 flex-col gap-3">
      <SectionHeading
        id="home-campaigns"
        title="Open campaigns"
        action={
          <Link href="/campaigns" className="text-link text-dense">
            All campaigns
          </Link>
        }
      />
      {campaigns.length === 0 ? (
        <p className="border-t py-6 text-ink-2">
          No open campaigns. Closed and archived ones are on the{" "}
          <Link href="/campaigns" className="text-link">
            Campaigns
          </Link>{" "}
          page.
        </p>
      ) : (
        <ul className="-mx-3 flex flex-col gap-0.5">
          {campaigns.map((campaign) => (
            <CampaignRow key={campaign.id} campaign={campaign} />
          ))}
        </ul>
      )}
    </section>
  )
}

/** "94 applicants · 4 strong · 2 new today": what the API counts, nothing more. */
function campaignSummary(counts: Campaign["counts"]): string {
  const parts = [plural(counts.applications, "applicant")]
  if (counts.strong_matches > 0) parts.push(`${counts.strong_matches} strong`)
  if (counts.new_resumes_24h > 0) parts.push(`${counts.new_resumes_24h} new today`)
  if (counts.in_progress > 0) parts.push(`${counts.in_progress} processing`)
  if (counts.failed > 0) parts.push(`${counts.failed} failed`)
  return parts.join(" · ")
}

function CampaignRow({ campaign }: { campaign: Campaign }) {
  const { counts } = campaign
  const strongShare = counts.applications > 0 ? (counts.strong_matches / counts.applications) * 100 : 0
  // "Senior Python Engineer, platform team" already says its role: don't repeat it.
  const showRole = !campaign.title.toLowerCase().includes(campaign.role.toLowerCase())
  return (
    <li className="relative flex flex-col gap-2 rounded-xl px-3 py-3 transition-colors focus-within:bg-muted/60 hover:bg-muted/60">
      <span className="flex min-w-0 flex-col gap-0.5">
        <Link
          href={campaignHref(campaign.id)}
          className="line-clamp-2 font-semibold break-words outline-none after:absolute after:inset-0 after:rounded-xl focus-visible:underline"
          title={campaign.title}
        >
          {campaign.title}
        </Link>
        {showRole && <span className="truncate text-dense text-muted-foreground">{campaign.role}</span>}
      </span>
      {/* Strong matches as a share of the applicants (the sentence below says the numbers). */}
      <span aria-hidden="true" className="flex h-1.5 overflow-hidden rounded-full bg-border">
        <span
          className={cn("h-full w-(--share) rounded-full", categoryBarClass("strong_match"))}
          style={{ "--share": `${strongShare}%` } as CSSProperties}
        />
      </span>
      <span className="text-dense text-ink-2">{campaignSummary(counts)}</span>
    </li>
  )
}

function Processing({ processing }: { processing: HomeData["processing"] }) {
  const { total, items } = processing
  return (
    <section aria-label="Processing" className="flex flex-col gap-2 text-dense text-muted-foreground">
      {total === 0 ? (
        <p className="flex items-center gap-2">
          <span aria-hidden="true" className="size-2 shrink-0 rounded-full bg-primary" />
          All uploads are processed.
        </p>
      ) : (
        <>
          <p className="flex items-center gap-2">
            <LoaderCircleIcon className="size-3.5 shrink-0 animate-spin text-primary motion-reduce:animate-none" aria-hidden="true" />
            {plural(total, "resume")} {total === 1 ? "is" : "are"} being read and scored.
          </p>
          <ul className="flex flex-col gap-1 pl-5.5">
            {items.map((resume) => (
              <ProcessingItem key={resume.id} resume={resume} />
            ))}
          </ul>
          {total > items.length && <p className="pl-5.5">Showing the {items.length} newest.</p>}
        </>
      )}
    </section>
  )
}

function ProcessingItem({ resume }: { resume: ProcessingResume }) {
  return (
    <li className="flex min-w-0 items-baseline gap-1.5">
      <span className="truncate text-ink-2" title={resume.filename}>
        {resume.filename}
      </span>
      <span className="shrink-0">· {RESUME_STATUS_LABELS[resume.status]}</span>
      {resume.campaign ? (
        <Link href={campaignHref(resume.campaign.id, "uploads")} className="ml-auto shrink-0 truncate hover:text-foreground hover:underline">
          {resume.campaign.title}
          <span className="sr-only">: uploads</span>
        </Link>
      ) : (
        <span className="ml-auto shrink-0">Deleted campaign</span>
      )}
    </li>
  )
}

function RelativeTime({ iso }: { iso: string }) {
  const date = new Date(iso)
  return (
    <time dateTime={iso} title={date.toLocaleString()}>
      {formatDistanceToNowStrict(date, { addSuffix: true })}
    </time>
  )
}

/** The final layout's shape: queue rows on the left, campaign rows on the right. */
function HomeSkeleton() {
  return (
    <div className="flex flex-wrap items-start gap-x-12 gap-y-10" aria-busy="true" aria-label="Loading home">
      <div className="flex min-w-0 flex-[999_1_520px] flex-col gap-3">
        <Skeleton className="h-5 w-36" />
        <div className="border-t">
          {Array.from({ length: 5 }, (_, index) => (
            <div key={index} className="flex items-center gap-4 border-b px-1 py-3.5">
              <Skeleton className="size-9 rounded-full" />
              <div className="flex flex-1 flex-col gap-1.5">
                <Skeleton className="h-4 w-40" />
                <Skeleton className="h-3 w-28" />
              </div>
              <Skeleton className="hidden h-3 w-32 md:block" />
              <Skeleton className="h-4 w-20" />
            </div>
          ))}
        </div>
      </div>
      <div className="flex min-w-0 flex-[1_1_300px] flex-col gap-3">
        <Skeleton className="h-5 w-32" />
        {Array.from({ length: 4 }, (_, index) => (
          <div key={index} className="flex flex-col gap-2 py-3">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-1.5 w-full" />
            <Skeleton className="h-3 w-40" />
          </div>
        ))}
      </div>
    </div>
  )
}
