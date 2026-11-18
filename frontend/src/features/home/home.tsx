"use client"

import { formatDistanceToNow } from "date-fns"
import { ArrowRightIcon, BriefcaseIcon, CheckCircle2Icon, Loader2Icon, PlusIcon, SparklesIcon } from "lucide-react"
import Link from "next/link"

import { ScoreCell } from "@/components/application/score"
import { CampaignStatusBadge } from "@/components/campaign/status-badge"
import { EmptyState, ErrorState } from "@/components/data-states"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import type { Schema } from "@/lib/api/client"
import { displayName, stageLabel } from "@/lib/applications"
import { applicationHref, campaignHref } from "@/lib/routes"
import { cn } from "@/lib/utils"

import { useHome, useWorkspaceStats, type Home as HomeData, type ProcessingResume, type RecentMatch } from "./queries"

type Campaign = Schema<"Campaign">

/** A processing resume's status, in the Uploads tab's words. */
const RESUME_STATUS_LABELS: Record<Schema<"ResumeStatus">, string> = {
  pending: "Queued",
  extracting: "Extracting",
  processing: "Analysing",
  done: "Done",
  failed: "Failed",
  quota_exceeded: "Quota limit",
}

function isEmpty(home: HomeData): boolean {
  return home.active_campaigns.length === 0 && home.processing.total === 0 && home.recent_strong_matches.length === 0
}

/**
 * `/` (spec §4): what needs attention across campaigns. Active campaigns with
 * their new uploads and strong matches, resumes still processing, and the
 * latest strong matches, from `GET /home`. A first run (no campaigns at all)
 * invites creating one.
 */
export function Home() {
  const home = useHome()
  const empty = home.data !== undefined && isEmpty(home.data)
  const stats = useWorkspaceStats({ enabled: empty })

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-8 px-6 py-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold tracking-tight">Home</h1>
          <p className="text-muted-foreground">What needs your attention across campaigns.</p>
        </div>
        <NewCampaignButton />
      </header>

      {home.isPending || (empty && stats.isPending) ? (
        <HomeSkeleton />
      ) : home.isError ? (
        <ErrorState error={home.error} onRetry={() => void home.refetch()} retrying={home.isFetching} />
      ) : empty && stats.data?.campaigns.total === 0 ? (
        <EmptyState
          icon={BriefcaseIcon}
          title="Create your first campaign"
          description="A campaign is one opening: describe the role, upload resumes, and get a ranked shortlist of candidates."
          action={<NewCampaignButton />}
        />
      ) : (
        <div className="grid gap-8 lg:grid-cols-3">
          <ActiveCampaigns campaigns={home.data.active_campaigns} className="lg:col-span-3" />
          <StrongMatches matches={home.data.recent_strong_matches} className="lg:col-span-2" />
          <Processing processing={home.data.processing} />
        </div>
      )}
    </div>
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

function Section({
  title,
  description,
  action,
  className,
  children,
}: {
  title: string
  description?: React.ReactNode
  action?: React.ReactNode
  className?: string
  children: React.ReactNode
}) {
  const id = `home-${title.toLowerCase().replace(/\W+/g, "-")}`
  return (
    <section aria-labelledby={id} className={cn("flex min-w-0 flex-col gap-3", className)}>
      <div className="flex items-end justify-between gap-4">
        <div className="flex flex-col gap-0.5">
          <h2 id={id} className="text-base font-medium">
            {title}
          </h2>
          {description && <p className="text-sm text-muted-foreground">{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

/** A section with nothing to list: one quiet line, not a full empty state. */
function Quiet({ icon: Icon, children }: { icon: typeof CheckCircle2Icon; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-dashed px-4 py-6 text-sm text-muted-foreground">
      <Icon className="size-4 shrink-0" aria-hidden="true" />
      <p>{children}</p>
    </div>
  )
}

function SeeAll({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Button asChild variant="link" size="sm" className="h-auto px-0">
      <Link href={href}>
        {children}
        <ArrowRightIcon data-icon="inline-end" />
      </Link>
    </Button>
  )
}

function ActiveCampaigns({ campaigns, className }: { campaigns: Campaign[]; className?: string }) {
  return (
    <Section
      title="Active campaigns"
      description="The most recently active ones."
      action={<SeeAll href="/campaigns">All campaigns</SeeAll>}
      className={className}
    >
      {campaigns.length === 0 ? (
        <Quiet icon={BriefcaseIcon}>
          No active campaigns. Closed and archived ones are on the{" "}
          <Link href="/campaigns" className="text-primary underline-offset-4 hover:underline">
            Campaigns
          </Link>{" "}
          page.
        </Quiet>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {campaigns.map((campaign) => (
            <li key={campaign.id}>
              <CampaignCard campaign={campaign} />
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}

function CampaignCard({ campaign }: { campaign: Campaign }) {
  const { counts } = campaign
  return (
    // The title link covers the card (its ::after), so the whole card is one target.
    <article className="relative flex h-full flex-col gap-3 rounded-lg border p-4 transition-colors focus-within:ring-2 focus-within:ring-ring hover:bg-muted/40 motion-reduce:transition-none">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h3 className="truncate font-medium" title={campaign.title}>
            <Link href={campaignHref(campaign.id)} className="outline-none after:absolute after:inset-0 after:rounded-lg">
              {campaign.title}
            </Link>
          </h3>
          <p className="truncate text-sm text-muted-foreground">{campaign.role}</p>
        </div>
        <CampaignStatusBadge status={campaign.status} className="shrink-0" />
      </div>
      <dl className="grid grid-cols-3 gap-2">
        <Count label="New in 24 h" value={counts.new_resumes_24h} />
        <Count label="Strong matches" value={counts.strong_matches} tone={counts.strong_matches > 0 ? "strong" : undefined} />
        <Count
          label="Processing"
          value={counts.in_progress}
          tone={counts.in_progress > 0 ? "active" : undefined}
          hint={counts.failed > 0 ? `${counts.failed} failed` : undefined}
        />
      </dl>
      <p className="mt-auto text-xs text-muted-foreground">
        Last activity <RelativeTime iso={campaign.last_activity_at} />
      </p>
    </article>
  )
}

function Count({
  label,
  value,
  tone,
  hint,
}: {
  label: string
  value: number
  tone?: "strong" | "active"
  hint?: string
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd
        className={cn(
          "text-lg font-semibold tabular-nums",
          value === 0 && "text-muted-foreground",
          tone === "strong" && "text-category-strong-foreground",
          tone === "active" && "text-primary",
        )}
      >
        {value}
        {hint && <span className="ml-1.5 text-xs font-normal text-destructive">{hint}</span>}
      </dd>
    </div>
  )
}

function StrongMatches({ matches, className }: { matches: RecentMatch[]; className?: string }) {
  return (
    <Section title="Recent strong matches" description="The newest candidates the AI rated a strong match." className={className}>
      {matches.length === 0 ? (
        <Quiet icon={SparklesIcon}>No strong matches yet. They appear here as resumes are scored.</Quiet>
      ) : (
        <div className="rounded-lg border">
          <Table className="text-dense">
            <TableCaption className="sr-only">Recent strong matches, newest first. Open a candidate by their name.</TableCaption>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="pl-4">Candidate</TableHead>
                <TableHead>Campaign</TableHead>
                <TableHead>Score</TableHead>
                <TableHead>Stage</TableHead>
                <TableHead className="pr-4">Scored</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {matches.map((match) => (
                <TableRow key={match.id}>
                  <TableCell className="max-w-48 pl-4">
                    <Link
                      href={applicationHref(match.campaign.id, match.id)}
                      className="block truncate rounded-sm font-medium outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      {displayName(match.person.name)}
                    </Link>
                  </TableCell>
                  <TableCell className="max-w-48 truncate text-muted-foreground" title={match.campaign.title}>
                    {match.campaign.title}
                  </TableCell>
                  <TableCell>
                    <ScoreCell score={match.score} category={match.category} />
                  </TableCell>
                  <TableCell className={cn(match.pipeline_stage === null && "text-muted-foreground")}>
                    {stageLabel(match.pipeline_stage)}
                  </TableCell>
                  <TableCell className="pr-4 whitespace-nowrap text-muted-foreground">
                    <RelativeTime iso={match.created_at} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </Section>
  )
}

function Processing({ processing }: { processing: HomeData["processing"] }) {
  const { total, items } = processing
  return (
    <Section
      title="Processing"
      description={total === 0 ? "Uploads still being read and scored." : `${total} ${total === 1 ? "resume" : "resumes"} in progress.`}
    >
      {total === 0 ? (
        <Quiet icon={CheckCircle2Icon}>Nothing is processing. Every upload is done.</Quiet>
      ) : (
        <div className="flex flex-col gap-2">
          <ul className="divide-y rounded-lg border">
            {items.map((resume) => (
              <ProcessingItem key={resume.id} resume={resume} />
            ))}
          </ul>
          {total > items.length && (
            <p className="text-xs text-muted-foreground">
              Showing the {items.length} newest of {total}.
            </p>
          )}
        </div>
      )}
    </Section>
  )
}

function ProcessingItem({ resume }: { resume: ProcessingResume }) {
  return (
    <li className="flex items-center justify-between gap-3 px-3 py-2.5 text-dense">
      <div className="flex min-w-0 flex-col">
        <span className="truncate font-medium" title={resume.filename}>
          {resume.filename}
        </span>
        {resume.campaign ? (
          <Link
            href={campaignHref(resume.campaign.id, "uploads")}
            className="truncate text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
          >
            {resume.campaign.title}
            <span className="sr-only">: uploads</span>
          </Link>
        ) : (
          <span className="text-xs text-muted-foreground">Deleted campaign</span>
        )}
      </div>
      <span className="inline-flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
        <Loader2Icon className="size-3.5 animate-spin text-primary motion-reduce:animate-none" aria-hidden="true" />
        {RESUME_STATUS_LABELS[resume.status]}
      </span>
    </li>
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

function HomeSkeleton() {
  return (
    <div className="grid gap-8 lg:grid-cols-3" aria-busy="true" aria-label="Loading home">
      <div className="flex flex-col gap-3 lg:col-span-3">
        <Skeleton className="h-5 w-40" />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }, (_, index) => (
            <Skeleton key={index} className="h-36 w-full rounded-lg" />
          ))}
        </div>
      </div>
      <div className="flex flex-col gap-3 lg:col-span-2">
        <Skeleton className="h-5 w-48" />
        <Skeleton className="h-56 w-full rounded-lg" />
      </div>
      <div className="flex flex-col gap-3">
        <Skeleton className="h-5 w-32" />
        <Skeleton className="h-56 w-full rounded-lg" />
      </div>
    </div>
  )
}
