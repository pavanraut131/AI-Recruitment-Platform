"use client"

import Link from "next/link"
import { useQueryState } from "nuqs"
import { ArrowLeftIcon, SearchXIcon, UploadIcon } from "lucide-react"

import { CampaignStatusBadge } from "@/components/campaign/status-badge"
import { EmptyState, ErrorState } from "@/components/data-states"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { isApiProblem } from "@/lib/api/client"
import { useCampaignEvents, type CampaignEventsStatus } from "@/lib/query/use-campaign-events"
import { cn } from "@/lib/utils"

import { useCampaign, type Campaign } from "../api"
import { InsightsTab } from "../insights/insights-tab"
import { CampaignSummary } from "./campaign-summary"
import { SettingsTab } from "./settings-tab"
import { WORKSPACE_TAB_LABELS, WORKSPACE_TABS, workspaceTabParser, type WorkspaceTab } from "./workspace-tabs"

/**
 * `/campaigns/[id]` (spec §4, docs/design/source/Candidates.dc.html): the
 * header with one summary sentence, then the tabs. The tab is in the URL
 * (`?tab=`, default candidates). The shell collapses the sidebar to an icon
 * rail here, so the table and the review panel get the width.
 *
 * `candidatesTab` and `uploadsTab` are the Candidates and Uploads tabs'
 * content, passed in by the route so the applications (E1-05) and uploads
 * (E1-06) features plug in without this feature importing them.
 *
 * The workspace mounts the campaign's live event stream once
 * (`useCampaignEvents`); its tabs only read the Query cache it keeps current.
 */
export function CampaignWorkspace({
  campaignId,
  candidatesTab,
  uploadsTab,
}: {
  campaignId: string
  candidatesTab: React.ReactNode
  uploadsTab: React.ReactNode
}) {
  const campaign = useCampaign(campaignId)
  const live = useCampaignEvents(campaignId, { enabled: campaign.data !== undefined })
  const [tab, setTab] = useQueryState("tab", workspaceTabParser)

  if (campaign.isPending) return <WorkspaceSkeleton />

  if (campaign.isError) {
    const notFound = isApiProblem(campaign.error) && (campaign.error.status === 404 || campaign.error.status === 422)
    return (
      <WorkspaceFrame>
        {notFound ? (
          <EmptyState
            icon={SearchXIcon}
            title="Campaign not found"
            description="It may have been deleted, or the link is wrong. Pick it from the campaign list."
            action={<BackToCampaigns />}
          />
        ) : (
          <ErrorState
            error={campaign.error}
            onRetry={() => void campaign.refetch()}
            retrying={campaign.isFetching}
            action={<BackToCampaigns />}
          />
        )}
      </WorkspaceFrame>
    )
  }

  const data = campaign.data
  return (
    <WorkspaceFrame>
      <WorkspaceHeader campaign={data} live={live} onTab={(next) => void setTab(next)} />
      <Tabs value={tab} onValueChange={(value) => void setTab(value as WorkspaceTab)} className="gap-5">
        <TabsList
          variant="line"
          aria-label="Campaign sections"
          className="h-auto! w-full justify-start gap-6 overflow-x-auto border-b p-0"
        >
          {WORKSPACE_TABS.map((value) => (
            <TabsTrigger
              key={value}
              value={value}
              className="h-auto flex-none rounded-none px-0 py-2.5 text-muted-foreground after:bottom-[-1px]! data-active:font-semibold data-active:text-foreground"
            >
              {WORKSPACE_TAB_LABELS[value]}
              {value === "candidates" && data.counts.applications > 0 && (
                <span aria-hidden="true" className="font-medium text-muted-foreground">
                  {data.counts.applications}
                </span>
              )}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="candidates">{candidatesTab}</TabsContent>
        <TabsContent value="uploads">{uploadsTab}</TabsContent>
        <TabsContent value="insights">
          <InsightsTab campaign={data} onUpload={() => void setTab("uploads")} />
        </TabsContent>
        <TabsContent value="settings">
          <SettingsTab campaign={data} />
        </TabsContent>
      </Tabs>
    </WorkspaceFrame>
  )
}

function WorkspaceFrame({ children }: { children: React.ReactNode }) {
  return <div className="flex w-full max-w-[1440px] flex-col gap-5 px-4 pt-3 pb-10 sm:px-6 lg:px-10">{children}</div>
}

function BackToCampaigns() {
  return (
    <Button variant="outline" asChild>
      <Link href="/campaigns">
        <ArrowLeftIcon data-icon="inline-start" />
        All campaigns
      </Link>
    </Button>
  )
}

/** "12 Nov" (this year) or "12 Nov 2025". */
function shortDate(iso: string): string {
  const date = new Date(iso)
  const sameYear = date.getFullYear() === new Date().getFullYear()
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }) })
}

function WorkspaceHeader({
  campaign,
  live,
  onTab,
}: {
  campaign: Campaign
  live: CampaignEventsStatus
  onTab: (tab: WorkspaceTab) => void
}) {
  return (
    <header className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="flex min-w-0 flex-col gap-1">
          <h1 className="text-title font-semibold tracking-tight break-words">{campaign.title}</h1>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-dense text-muted-foreground">
            <span className="text-ink-2">{campaign.role}</span>
            <MetaSeparator />
            <CampaignStatusBadge status={campaign.status} />
            <MetaSeparator />
            <span>Created {shortDate(campaign.created_at)}</span>
            {live !== "idle" && <MetaSeparator />}
            <LiveIndicator status={live} />
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" onClick={() => onTab("settings")} className="text-ink-2">
            Edit brief
          </Button>
          <Button onClick={() => onTab("uploads")}>
            <UploadIcon data-icon="inline-start" />
            Upload resumes
          </Button>
        </div>
      </div>
      <CampaignSummary campaign={campaign} onTab={onTab} />
    </header>
  )
}

/** A quiet middle dot between the header's facts, so the status dots don't run together. */
function MetaSeparator() {
  return (
    <span aria-hidden="true" className="text-muted-foreground/60">
      ·
    </span>
  )
}

/** Whether processing updates are arriving live (the campaign's SSE stream). */
function LiveIndicator({ status }: { status: CampaignEventsStatus }) {
  if (status === "idle") return null
  const label = status === "open" ? "Live" : status === "reconnecting" ? "Reconnecting…" : "Connecting…"
  return (
    <span
      role="status"
      className="inline-flex items-center gap-1.5 text-dense text-muted-foreground"
      title="Processing updates arrive live while this page is open"
    >
      <span
        aria-hidden="true"
        className={cn("size-1.5 rounded-full", status === "open" ? "bg-primary" : "bg-muted-foreground")}
      />
      {label}
    </span>
  )
}

/** The header, the sentence, the tabs and the table's first rows, as they will sit. */
function WorkspaceSkeleton() {
  return (
    <WorkspaceFrame>
      <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading campaign">
        <Skeleton className="h-8 w-80 max-w-full" />
        <Skeleton className="h-4 w-56" />
        <Skeleton className="mt-1.5 h-4 w-[28rem] max-w-full" />
      </div>
      <div className="flex gap-6 border-b pb-2.5">
        {Array.from({ length: 4 }, (_, index) => (
          <Skeleton key={index} className="h-4 w-20" />
        ))}
      </div>
      <div className="flex gap-2">
        <Skeleton className="h-9 w-72" />
        <Skeleton className="h-9 w-24" />
        <Skeleton className="h-9 w-24" />
      </div>
      <div className="flex flex-col border-t">
        {Array.from({ length: 6 }, (_, index) => (
          <div key={index} className="flex items-center gap-6 border-b px-3 py-3.5">
            <Skeleton className="size-4" />
            <div className="flex w-52 flex-col gap-1.5">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-3 w-44" />
            </div>
            <Skeleton className="h-3.5 w-40" />
            <Skeleton className="h-3.5 w-12" />
          </div>
        ))}
      </div>
    </WorkspaceFrame>
  )
}
