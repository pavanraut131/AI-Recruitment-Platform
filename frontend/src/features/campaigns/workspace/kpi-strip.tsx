"use client"

import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

import { useCampaignStats, type Campaign } from "../api"

/**
 * The workspace's headline numbers: counts come with the campaign, the
 * average score from its stats (`GET /campaigns/{id}/stats`). Both refresh
 * when a resume finishes processing (`useCampaignEvents`).
 */
export function KpiStrip({ campaign }: { campaign: Campaign }) {
  const stats = useCampaignStats(campaign.id)
  const { counts } = campaign

  const avgScore = stats.isPending ? (
    <Skeleton className="h-6 w-10" aria-label="Loading" />
  ) : stats.isError || stats.data.avg_score === null ? (
    <span className="text-muted-foreground" title={stats.isError ? "Could not load the average score" : "No scored applications yet"}>
      —
    </span>
  ) : (
    Math.round(stats.data.avg_score)
  )

  return (
    <dl className="grid grid-cols-2 rounded-lg border sm:grid-cols-3 lg:grid-cols-6 lg:divide-x">
      <Kpi label="Resumes" value={counts.resumes} hint={counts.new_resumes_24h > 0 ? `${counts.new_resumes_24h} in the last 24 h` : undefined} />
      <Kpi label="Applications" value={counts.applications} hint="Scored by the AI" />
      <Kpi label="Strong matches" value={counts.strong_matches} tone={counts.strong_matches > 0 ? "strong" : undefined} />
      <Kpi label="Average score" value={avgScore} hint="Out of 100" />
      <Kpi label="Processing" value={counts.in_progress} tone={counts.in_progress > 0 ? "active" : undefined} />
      <Kpi label="Failed" value={counts.failed} tone={counts.failed > 0 ? "failed" : undefined} hint="Incl. AI quota" />
    </dl>
  )
}

function Kpi({
  label,
  value,
  hint,
  tone,
}: {
  label: string
  value: React.ReactNode
  hint?: string
  tone?: "strong" | "active" | "failed"
}) {
  return (
    <div className="flex flex-col gap-1 px-4 py-3">
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd
        className={cn(
          "text-xl font-semibold tabular-nums",
          tone === "strong" && "text-category-strong-foreground",
          tone === "active" && "text-primary",
          tone === "failed" && "text-destructive",
        )}
      >
        {value}
      </dd>
      {hint && <dd className="text-xs text-muted-foreground">{hint}</dd>}
    </div>
  )
}
