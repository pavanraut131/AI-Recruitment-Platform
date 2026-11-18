/**
 * Links between screens that belong to different features (the review panel
 * links to the Person profile and back, both link to campaigns), so neither
 * feature hard-codes the other's URL keys.
 */

/** An application in its campaign: the Candidates tab with its review panel open (`open`, E1-05). */
export function applicationHref(campaignId: string, applicationId: string): string {
  return `/campaigns/${encodeURIComponent(campaignId)}?open=${encodeURIComponent(applicationId)}`
}

/** A Person's profile, optionally showing one of their applications (`application`, E1-07). */
export function personHref(personId: string, applicationId?: string): string {
  const path = `/people/${encodeURIComponent(personId)}`
  return applicationId ? `${path}?application=${encodeURIComponent(applicationId)}` : path
}

/** A campaign's workspace. */
export function campaignHref(campaignId: string): string {
  return `/campaigns/${encodeURIComponent(campaignId)}`
}
