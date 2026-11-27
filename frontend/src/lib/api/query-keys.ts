import type { QueryParams } from "./client"

/**
 * TanStack Query keys, one factory per resource (ADR 0007).
 *
 * Keys are hierarchical arrays, so a prefix invalidates everything below it:
 * `campaigns.all` → every campaign query, `campaigns.detail(id)` → that
 * campaign and its stats, `applications.campaignLists(campaignId)` →
 * every filtered page of one campaign's applications. List params are the request's query
 * parameters, kept as one object at the end of the key: Query hashes keys
 * structurally, so `{page: 2, q: "a"}` and `{q: "a", page: 2}` are the same key.
 *
 * Use these everywhere (queries, `setQueryData`, invalidation); never write a
 * key by hand.
 */

export type CampaignListParams = QueryParams<"listCampaigns">
export type ApplicationListParams = QueryParams<"listCampaignApplications">
export type ResumeListParams = QueryParams<"listCampaignResumes">
export type PeopleListParams = QueryParams<"listPeople">

// Each factory spreads its own root constant (not `campaigns.all`), so the
// object's type does not depend on itself.
const CAMPAIGNS = ["campaigns"] as const
const campaigns = {
  all: CAMPAIGNS,
  lists: () => [...CAMPAIGNS, "list"] as const,
  list: (params: CampaignListParams = {}) => [...CAMPAIGNS, "list", params] as const,
  details: () => [...CAMPAIGNS, "detail"] as const,
  detail: (campaignId: string) => [...CAMPAIGNS, "detail", campaignId] as const,
  /** Below the campaign's detail key: invalidating the campaign refreshes its stats too. */
  stats: (campaignId: string) => [...CAMPAIGNS, "detail", campaignId, "stats"] as const,
}

const APPLICATIONS = ["applications"] as const
const applications = {
  all: APPLICATIONS,
  /** Every list (any campaign, any filters). */
  lists: () => [...APPLICATIONS, "list"] as const,
  /** Every list of one campaign, whatever its filters. */
  campaignLists: (campaignId: string) => [...APPLICATIONS, "list", campaignId] as const,
  list: (campaignId: string, params: ApplicationListParams = {}) =>
    [...APPLICATIONS, "list", campaignId, params] as const,
  details: () => [...APPLICATIONS, "detail"] as const,
  detail: (applicationId: string) => [...APPLICATIONS, "detail", applicationId] as const,
}

const RESUMES = ["resumes"] as const
const resumes = {
  all: RESUMES,
  lists: () => [...RESUMES, "list"] as const,
  campaignLists: (campaignId: string) => [...RESUMES, "list", campaignId] as const,
  list: (campaignId: string, params: ResumeListParams = {}) =>
    [...RESUMES, "list", campaignId, params] as const,
}

const PEOPLE = ["people"] as const
const people = {
  all: PEOPLE,
  lists: () => [...PEOPLE, "list"] as const,
  list: (params: PeopleListParams = {}) => [...PEOPLE, "list", params] as const,
  details: () => [...PEOPLE, "detail"] as const,
  detail: (personId: string) => [...PEOPLE, "detail", personId] as const,
}

/** `GET /home`: the Home screen's aggregate. */
const home = {
  all: ["home"] as const,
}

/** `GET /stats`: workspace-wide counts. */
const stats = {
  all: ["stats"] as const,
}

/**
 * `GET /me`: the signed-in user, their agencies and the active one (EPIC-02
 * §9.4). The auth gate loads it; signing in, out or switching agency clears
 * the whole cache instead of invalidating it.
 */
const me = {
  all: ["me"] as const,
}

export const queryKeys = { campaigns, applications, resumes, people, home, stats, me } as const
