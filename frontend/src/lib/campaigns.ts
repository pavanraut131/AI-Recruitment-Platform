import type { Schema } from "@/lib/api/client"

/**
 * The vocabulary of a campaign's status, shared by every screen that shows
 * one (the campaign list and workspace in `features/campaigns`, Home in
 * `features/home`).
 */

export type CampaignStatus = Schema<"CampaignStatus">

/** In the order the UI lists them (the API's `CampaignStatus` enum). */
export const CAMPAIGN_STATUSES = ["active", "closed", "archived"] as const satisfies readonly CampaignStatus[]

export const CAMPAIGN_STATUS_LABELS: Record<CampaignStatus, string> = {
  active: "Active",
  closed: "Closed",
  archived: "Archived",
}
