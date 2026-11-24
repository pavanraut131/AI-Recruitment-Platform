import type { Schema } from "@/lib/api/client"

/*
 * Home's words (docs/design/README.md, voice): the greeting, the date and the
 * one summary sentence, built only from numbers the API gives.
 */

export type Greeting = "Good morning" | "Good afternoon" | "Good evening"

/** By the reader's local hour: morning until 12, afternoon until 18, then evening. */
export function greeting(hour: number): Greeting {
  if (hour < 12) return "Good morning"
  if (hour < 18) return "Good afternoon"
  return "Good evening"
}

/** "Tuesday 24 November" (en-GB, the reader's time zone). */
export function longDate(date: Date): string {
  return date.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" })
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count.toLocaleString("en-GB")} ${count === 1 ? one : many}`
}

export type SummaryParts = {
  /** The figure the sentence leads with, set in ink (the rest is ink-2). */
  lead: string
  rest: string
}

/**
 * The one sentence under the greeting. Leads with the candidates waiting for
 * a first look (applications with no stage, across campaigns); adds the
 * resumes of the last 24 hours only when Home lists every open campaign
 * (otherwise the sum would undercount). `null` when there is no campaign at
 * all (the first-run invitation takes over).
 */
export function summarySentence(home: Schema<"Home">, stats: Schema<"Stats">): SummaryParts | null {
  const open = stats.campaigns.active
  if (stats.campaigns.total === 0) return null
  if (open === 0) {
    return { lead: "No campaigns are open.", rest: " Start one when a new role comes in, or reopen one from Campaigns." }
  }
  const waiting = stats.applications.by_stage.unreviewed
  const listsEveryCampaign = home.active_campaigns.length >= open
  const newResumes = listsEveryCampaign
    ? home.active_campaigns.reduce((sum, campaign) => sum + campaign.counts.new_resumes_24h, 0)
    : 0
  const across = `across ${plural(open, "open campaign")}`
  if (waiting === 0) {
    return { lead: "Every candidate has had a first look", rest: ` ${across}.` }
  }
  const lead = `${plural(waiting, "candidate")} ${waiting === 1 ? "is" : "are"} waiting for a first look`
  if (newResumes > 0) {
    return {
      lead,
      rest: ` ${across}, and ${plural(newResumes, "resume")} came in over the last day.`,
    }
  }
  return { lead, rest: ` ${across}.` }
}
