"use client"

import Link from "next/link"
import { useQueryState } from "nuqs"
import { ArrowLeftIcon, PencilIcon, SearchXIcon, UploadIcon } from "lucide-react"

import { CampaignStatusBadge } from "@/components/campaign/status-badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { isApiProblem } from "@/lib/api/client"
import { useCampaignEvents, type CampaignEventsStatus } from "@/lib/query/use-campaign-events"
import { cn } from "@/lib/utils"

import { useCampaign, type Campaign } from "../api"
import { InsightsTab } from "../insights/insights-tab"
import { EmptyState, ErrorState } from "@/components/data-states"
import { KpiStrip } from "./kpi-strip"
import { SettingsTab } from "./settings-tab"
import { WORKSPACE_TAB_LABELS, WORKSPACE_TABS, workspaceTabParser, type WorkspaceTab } from "./workspace-tabs"

/**
 * `/campaigns/[id]`: header, KPI strip and tabs (spec §4). The tab is in the
 * URL (`?tab=`, default candidates).
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
            description="It may have been deleted, or the link is wrong."
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
      <KpiStrip campaign={data} />
      <Tabs value={tab} onValueChange={(value) => void setTab(value as WorkspaceTab)} className="gap-4">
        <TabsList variant="line" aria-label="Campaign sections" className="w-full justify-start border-b">
          {WORKSPACE_TABS.map((value) => (
            <TabsTrigger key={value} value={value} className="flex-none px-3">
              {WORKSPACE_TAB_LABELS[value]}
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
  return <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-6 py-8">{children}</div>
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
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div className="flex min-w-0 flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="truncate text-2xl font-semibold tracking-tight">{campaign.title}</h1>
          <CampaignStatusBadge status={campaign.status} />
          <LiveIndicator status={live} />
        </div>
        <p className="text-muted-foreground">{campaign.role}</p>
      </div>
      <div className="flex items-center gap-2">
        <Button variant="outline" onClick={() => onTab("settings")}>
          <PencilIcon data-icon="inline-start" />
          Edit
        </Button>
        <Button onClick={() => onTab("uploads")}>
          <UploadIcon data-icon="inline-start" />
          Upload resumes
        </Button>
      </div>
    </header>
  )
}

/** Whether processing updates are arriving live (the campaign's SSE stream). */
function LiveIndicator({ status }: { status: CampaignEventsStatus }) {
  if (status === "idle") return null
  const label = status === "open" ? "Live" : status === "reconnecting" ? "Reconnecting…" : "Connecting…"
  return (
    <span
      role="status"
      className="inline-flex items-center gap-1.5 text-xs text-muted-foreground"
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

function WorkspaceSkeleton() {
  return (
    <WorkspaceFrame>
      <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading campaign">
        <Skeleton className="h-7 w-72" />
        <Skeleton className="h-4 w-40" />
      </div>
      <div className="grid grid-cols-2 gap-px rounded-lg border sm:grid-cols-3 lg:grid-cols-6">
        {Array.from({ length: 6 }, (_, index) => (
          <div key={index} className="flex flex-col gap-2 px-4 py-3">
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-6 w-12" />
          </div>
        ))}
      </div>
      <Skeleton className="h-8 w-80" />
      <Skeleton className="h-64 w-full" />
    </WorkspaceFrame>
  )
}
