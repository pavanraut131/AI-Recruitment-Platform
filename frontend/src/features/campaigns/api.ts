"use client"

import {
  keepPreviousData,
  queryOptions,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query"

import { api, unwrap, type Schema } from "@/lib/api/client"
import { queryKeys, type CampaignListParams } from "@/lib/api/query-keys"

export type Campaign = Schema<"Campaign">
export type CampaignStats = Schema<"CampaignStats">
export type CampaignCreate = Schema<"CampaignCreate">
export type CampaignUpdate = Schema<"CampaignUpdate">
export type ParsedJobDescription = Schema<"ParsedJobDescription">

/*
 * Server state for campaigns (ADR 0007): every read is a query keyed by the
 * factories in `@/lib/api/query-keys`, every write a mutation that updates or
 * invalidates those keys. Request params and keys are built from the same
 * object, so a key never leaves out a parameter the request uses.
 */

export function campaignListQuery(params: CampaignListParams) {
  return queryOptions({
    queryKey: queryKeys.campaigns.list(params),
    queryFn: ({ signal }) => api.GET("/campaigns", { params: { query: params }, signal }).then(unwrap),
  })
}

export function campaignQuery(campaignId: string) {
  return queryOptions({
    queryKey: queryKeys.campaigns.detail(campaignId),
    queryFn: ({ signal }) =>
      api.GET("/campaigns/{campaign_id}", { params: { path: { campaign_id: campaignId } }, signal }).then(unwrap),
  })
}

export function campaignStatsQuery(campaignId: string) {
  return queryOptions({
    queryKey: queryKeys.campaigns.stats(campaignId),
    queryFn: ({ signal }) =>
      api
        .GET("/campaigns/{campaign_id}/stats", { params: { path: { campaign_id: campaignId } }, signal })
        .then(unwrap),
  })
}

/** One page of the campaign list. The previous page stays on screen while the next filter loads. */
export function useCampaigns(params: CampaignListParams) {
  return useQuery({ ...campaignListQuery(params), placeholderData: keepPreviousData })
}

export function useCampaign(campaignId: string) {
  return useQuery(campaignQuery(campaignId))
}

export function useCampaignStats(campaignId: string) {
  return useQuery(campaignStatsQuery(campaignId))
}

/** Everything a campaign's create, update or delete can change outside its own detail. */
function invalidateCampaignAggregates(queryClient: QueryClient) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.campaigns.lists() })
  void queryClient.invalidateQueries({ queryKey: queryKeys.home.all })
  void queryClient.invalidateQueries({ queryKey: queryKeys.stats.all })
}

/**
 * `POST /campaigns`. The form shows failures itself (field errors from a 422,
 * anything else above the buttons), so the global error toast is off.
 */
export function useCreateCampaign() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: CampaignCreate) => api.POST("/campaigns", { body }).then(unwrap),
    meta: { errorToast: false },
    onSuccess: (campaign) => {
      queryClient.setQueryData(queryKeys.campaigns.detail(campaign.id), campaign)
      invalidateCampaignAggregates(queryClient)
    },
  })
}

/** `PATCH /campaigns/{id}`: the response replaces the cached campaign. */
export function useUpdateCampaign(campaignId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: CampaignUpdate) =>
      api.PATCH("/campaigns/{campaign_id}", { params: { path: { campaign_id: campaignId } }, body }).then(unwrap),
    meta: { errorToast: false },
    onSuccess: (campaign) => {
      queryClient.setQueryData(queryKeys.campaigns.detail(campaignId), campaign)
      invalidateCampaignAggregates(queryClient)
    },
  })
}

/**
 * `DELETE /campaigns/{id}` (with its applications and resumes). The caller
 * navigates away; the campaign's own queries are marked stale without a
 * refetch (it would 404 while the workspace is still mounted), so coming back
 * to the URL shows "not found" instead of the cached campaign.
 */
export function useDeleteCampaign(campaignId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () =>
      api
        .DELETE("/campaigns/{campaign_id}", { params: { path: { campaign_id: campaignId } } })
        .then(unwrap),
    onSuccess: () => {
      for (const queryKey of [
        queryKeys.campaigns.detail(campaignId),
        queryKeys.applications.campaignLists(campaignId),
        queryKeys.resumes.campaignLists(campaignId),
      ]) {
        void queryClient.invalidateQueries({ queryKey, refetchType: "none" })
      }
      void queryClient.invalidateQueries({ queryKey: queryKeys.people.all })
      invalidateCampaignAggregates(queryClient)
    },
  })
}

/** `POST /campaigns/parse-jd`: the JD panel shows its own loading and error states. */
export function useParseJobDescription() {
  return useMutation({
    mutationFn: (jobDescription: string) =>
      api.POST("/campaigns/parse-jd", { body: { job_description: jobDescription } }).then(unwrap),
    meta: { errorToast: false },
  })
}
