import type { Schema } from "@/lib/api/client"

/*
 * Home's words (docs/design/README.md, voice): a short greeting by first name,
 * the date and the day's numbers, built only from numbers the API gives.
 */

/** "Tuesday 24 November" (en-GB, the reader's time zone). */
export function longDate(date: Date): string {
  return date.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" })
}

/** "Good morning" until noon (from 5am), "Good afternoon" until 6pm, "Good evening" otherwise; the reader's clock. */
export function timeOfDayGreeting(date: Date): string {
  const hour = date.getHours()
  if (hour >= 5 && hour < 12) return "Good morning"
  if (hour >= 12 && hour < 18) return "Good afternoon"
  return "Good evening"
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count.toLocaleString("en-GB")} ${count === 1 ? one : many}`
}

/** One fact in Home's numbers row: a count set in ink (when there is one) and its words. */
export type HomeFact = { key: string; count: number | null; label: string }

/**
 * The day's numbers, as Home's quiet row under its title (the same pattern as
 * a campaign's numbers): the candidates waiting for a first look (applications
 * with no stage, across campaigns), the open campaigns, and the resumes of the
 * last 24 hours, the last only when Home lists every open campaign (otherwise
 * the sum would undercount). `null` when there is no campaign at all (the
 * first-run invitation takes over).
 */
export function homeFacts(home: Schema<"Home">, stats: Schema<"Stats">): HomeFact[] | null {
  const open = stats.campaigns.active
  if (stats.campaigns.total === 0) return null
  if (open === 0) return [{ key: "open", count: null, label: "No campaigns are open" }]
  const waiting = stats.applications.by_stage.unreviewed
  const listsEveryCampaign = home.active_campaigns.length >= open
  const newResumes = listsEveryCampaign
    ? home.active_campaigns.reduce((sum, campaign) => sum + campaign.counts.new_resumes_24h, 0)
    : 0
  const facts: HomeFact[] = [
    waiting === 0
      ? { key: "waiting", count: null, label: "Everyone has had a first look" }
      : { key: "waiting", count: waiting, label: "waiting for a first look" },
    { key: "open", count: open, label: open === 1 ? "open campaign" : "open campaigns" },
  ]
  if (newResumes > 0) {
    facts.push({ key: "new", count: newResumes, label: newResumes === 1 ? "new resume today" : "new resumes today" })
  }
  return facts
}
