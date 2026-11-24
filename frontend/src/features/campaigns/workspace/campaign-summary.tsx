"use client"

import type { ReactNode } from "react"

import { CATEGORIES, CATEGORY_META } from "@/lib/applications"

import { useCampaignStats, type Campaign, type CampaignStats } from "../api"
import type { WorkspaceTab } from "./workspace-tabs"

function plural(count: number, one: string, many = `${one}s`): string {
  return `${count.toLocaleString("en-GB")} ${count === 1 ? one : many}`
}

/**
 * "3 strong, 9 possible and 12 unlikely": the categories that have anyone,
 * best first, plus the applications not scored yet. Leads with "No strong
 * matches yet" when there are none.
 */
export function categoryBreakdown(stats: CampaignStats): string {
  const byCategory = stats.applications.by_category
  const parts = CATEGORIES.filter((category) => byCategory[category] > 0).map(
    (category) => `${byCategory[category].toLocaleString("en-GB")} ${CATEGORY_META[category].short}`,
  )
  const scored = CATEGORIES.reduce((sum, category) => sum + byCategory[category], 0)
  const unscored = stats.applications.total - scored
  if (unscored > 0) parts.push(`${unscored.toLocaleString("en-GB")} not scored yet`)
  if (parts.length === 0) return ""
  const list = parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`
  return byCategory.strong_match === 0 ? `No strong matches yet: ${list}.` : `${list[0].toUpperCase()}${list.slice(1)}.`
}

/**
 * The workspace's numbers as one sentence under the title (no KPI cards):
 * applicants, how they split by match, the average score, and anything still
 * processing or failed, each linking to the tab that deals with it. Counts
 * come with the campaign, the split and the average from its stats; both
 * refresh as resumes finish (`useCampaignEvents`).
 */
export function CampaignSummary({ campaign, onTab }: { campaign: Campaign; onTab: (tab: WorkspaceTab) => void }) {
  const stats = useCampaignStats(campaign.id)
  const { applications, in_progress: inProgress, failed, resumes } = campaign.counts

  const toTab = (tab: WorkspaceTab, label: ReactNode) => (
    <button type="button" onClick={() => onTab(tab)} className="text-link cursor-pointer">
      {label}
    </button>
  )

  let lead: ReactNode
  let rest: ReactNode = null
  if (applications === 0) {
    lead = resumes === 0 ? "No applicants yet." : "No applicants scored yet."
    if (resumes === 0) rest = " Upload resumes to start the shortlist."
  } else {
    lead = `${plural(applications, "applicant")}.`
    if (stats.data) {
      const split = categoryBreakdown(stats.data)
      const average = stats.data.avg_score === null ? "" : ` Average score ${Math.round(stats.data.avg_score)}.`
      rest = ` ${split}${average}`
    }
  }

  return (
    <p className="max-w-3xl text-ink-2" data-slot="campaign-summary">
      <span className="font-semibold text-foreground">{lead}</span>
      {rest}
      {inProgress > 0 && ` ${plural(inProgress, "resume")} ${inProgress === 1 ? "is" : "are"} still being read and scored.`}
      {failed > 0 && (
        <>
          {" "}
          {plural(failed, "resume")} could not be processed: {toTab("uploads", "see Uploads")}.
        </>
      )}
    </p>
  )
}
