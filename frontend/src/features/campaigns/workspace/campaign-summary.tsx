"use client"

import type { ReactNode } from "react"

import { CATEGORIES, CATEGORY_META, SCORE_BAR_CLASS, type CategoryTone } from "@/lib/applications"
import { cn } from "@/lib/utils"

import { useCampaignStats, type Campaign, type CampaignStats } from "../api"
import type { WorkspaceTab } from "./workspace-tabs"

function plural(count: number, one: string, many = `${one}s`): string {
  return `${count.toLocaleString("en-GB")} ${count === 1 ? one : many}`
}

export interface SummaryItem {
  key: string
  count: number
  label: string
  tone?: CategoryTone
}

/**
 * How the scored applicants split by match, best first: "strong" always (a zero
 * there is the news), the other categories only when someone is in them, then
 * the applications not scored yet.
 */
export function categoryItems(stats: CampaignStats): SummaryItem[] {
  const byCategory = stats.applications.by_category
  const items: SummaryItem[] = CATEGORIES.filter(
    (category) => category === "strong_match" || byCategory[category] > 0,
  ).map((category) => ({
    key: category,
    count: byCategory[category],
    label: CATEGORY_META[category].short,
    tone: CATEGORY_META[category].tone,
  }))
  const scored = CATEGORIES.reduce((sum, category) => sum + byCategory[category], 0)
  const unscored = stats.applications.total - scored
  if (unscored > 0) items.push({ key: "unscored", count: unscored, label: "not scored yet" })
  return items
}

function Count({ children }: { children: ReactNode }) {
  return <span className="font-semibold text-foreground tabular-nums">{children}</span>
}

/**
 * The workspace's numbers under the title, as one quiet row (no KPI cards):
 * applicants, how they split by match (each with its colour dot), and the
 * average score. Anything still processing or failed follows as a short note
 * linking to the tab that deals with it. Counts come with the campaign, the
 * split and the average from its stats; both refresh as resumes finish
 * (`useCampaignEvents`).
 */
export function CampaignSummary({ campaign, onTab }: { campaign: Campaign; onTab: (tab: WorkspaceTab) => void }) {
  const stats = useCampaignStats(campaign.id)
  const { applications, in_progress: inProgress, failed, resumes } = campaign.counts

  const notes: ReactNode[] = []
  if (inProgress > 0) {
    notes.push(`${plural(inProgress, "resume")} ${inProgress === 1 ? "is" : "are"} still being read and scored.`)
  }
  if (failed > 0) {
    notes.push(
      <>
        {plural(failed, "resume")} could not be processed:{" "}
        <button type="button" onClick={() => onTab("uploads")} className="text-link cursor-pointer">
          see Uploads
        </button>
        .
      </>,
    )
  }

  return (
    <div className="flex flex-col gap-1.5" data-slot="campaign-summary">
      {applications === 0 ? (
        <p className="text-ink-2">
          <span className="font-semibold text-foreground">
            {resumes === 0 ? "No applicants yet." : "No applicants scored yet."}
          </span>
          {resumes === 0 && " Upload resumes to start the shortlist."}
        </p>
      ) : (
        <ul aria-label="Applicants" className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-ink-2">
          <li>
            <Count>{applications.toLocaleString("en-GB")}</Count> {applications === 1 ? "applicant" : "applicants"}
          </li>
          {stats.data &&
            categoryItems(stats.data).map((item) => (
              <li key={item.key} className="inline-flex items-center gap-1.5">
                <span
                  aria-hidden="true"
                  className={cn(
                    "size-2 shrink-0 rounded-full",
                    item.tone ? SCORE_BAR_CLASS[item.tone] : "bg-muted-foreground/40",
                  )}
                />
                <span>
                  <Count>{item.count.toLocaleString("en-GB")}</Count> {item.label}
                </span>
              </li>
            ))}
          {stats.data && stats.data.avg_score !== null && (
            <li className="border-l pl-5">
              Average score <Count>{Math.round(stats.data.avg_score)}</Count>
            </li>
          )}
        </ul>
      )}
      {notes.length > 0 && (
        <p className="text-dense text-muted-foreground">
          {notes.map((note, index) => (
            <span key={index}>
              {index > 0 && " "}
              {note}
            </span>
          ))}
        </p>
      )}
    </div>
  )
}
