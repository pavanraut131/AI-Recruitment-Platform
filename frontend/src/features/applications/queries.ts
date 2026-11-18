import { keepPreviousData, queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { api, unwrap, type QueryParams } from "@/lib/api/client"
import { queryKeys, type ApplicationListParams } from "@/lib/api/query-keys"
import { applicationQueryOptions } from "@/lib/query/applications"

import {
  REJECTED_STAGE,
  STAGE_LABELS,
  type ApplicationPage,
  type BulkResult,
  type Stage,
} from "./model"

/** One page of a campaign's applications (server-side filters, sort and paging). */
export function applicationsListOptions(campaignId: string, params: ApplicationListParams) {
  return queryOptions({
    queryKey: queryKeys.applications.list(campaignId, params),
    queryFn: ({ signal }) =>
      api
        .GET("/campaigns/{campaign_id}/applications", {
          params: { path: { campaign_id: campaignId }, query: params },
          signal,
        })
        .then(unwrap),
  })
}

export function useApplicationsList(campaignId: string, params: ApplicationListParams) {
  return useQuery({
    ...applicationsListOptions(campaignId, params),
    // Keep the current rows on screen while the next page or filter loads.
    placeholderData: keepPreviousData,
  })
}

/** The campaign, for its required skills (highlighted in the skills column). */
export function useCampaign(campaignId: string) {
  return useQuery({
    queryKey: queryKeys.campaigns.detail(campaignId),
    queryFn: ({ signal }) =>
      api.GET("/campaigns/{campaign_id}", { params: { path: { campaign_id: campaignId } }, signal }).then(unwrap),
  })
}

/** One application, for a panel opened from a link whose row is not on the current page. */
export function useApplication(applicationId: string | null, enabled: boolean) {
  return useQuery({
    ...applicationQueryOptions(applicationId ?? ""),
    enabled: enabled && applicationId !== null,
  })
}

/** The bulk actions this table offers (delete arrives with the review flow, not here). */
export type BulkInput = { action: "stage"; ids: string[]; stage: Stage } | { action: "reject"; ids: string[] }

type BulkContext = { previous: [readonly unknown[], ApplicationPage | undefined][] }

/** The stage a bulk action moves its applications to. */
export function targetStage(input: BulkInput): Stage {
  return input.action === "stage" ? input.stage : REJECTED_STAGE
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`
}

/** The toast for a finished bulk action, including ids the server did not find. */
export function bulkResultMessage(input: BulkInput, result: BulkResult): { title: string; description?: string } {
  const affected = result.affected.length
  const title =
    input.action === "stage"
      ? `Moved ${plural(affected, "candidate")} to ${STAGE_LABELS[input.stage]}`
      : `Rejected ${plural(affected, "candidate")}`
  const missing = result.not_found.length
  if (missing === 0) return { title }
  return {
    title,
    description: `${plural(missing, "candidate")} ${missing === 1 ? "was" : "were"} not found in this campaign (deleted meanwhile?) and ${missing === 1 ? "was" : "were"} skipped.`,
  }
}

/**
 * Move to stage / reject, optimistically (ADR 0007): every cached page of the
 * campaign shows the new stage at once, is restored if the request fails (the
 * global mutation handler toasts the problem), and is refetched afterwards.
 */
export function useBulkAction(campaignId: string, { onSuccess }: { onSuccess?: () => void } = {}) {
  const queryClient = useQueryClient()
  const listsKey = queryKeys.applications.campaignLists(campaignId)

  return useMutation<BulkResult, Error, BulkInput, BulkContext>({
    mutationFn: (input) =>
      api
        .POST("/campaigns/{campaign_id}/applications/bulk", {
          params: { path: { campaign_id: campaignId } },
          body: input,
        })
        .then(unwrap),
    onMutate: async (input) => {
      // A refetch in flight would overwrite the optimistic rows with old ones.
      await queryClient.cancelQueries({ queryKey: listsKey })
      const previous = queryClient.getQueriesData<ApplicationPage>({ queryKey: listsKey })
      const ids = new Set(input.ids)
      const stage = targetStage(input)
      queryClient.setQueriesData<ApplicationPage>({ queryKey: listsKey }, (page) =>
        page
          ? {
              ...page,
              items: page.items.map((item) => (ids.has(item.id) ? { ...item, pipeline_stage: stage } : item)),
            }
          : page,
      )
      return { previous }
    },
    onError: (_error, _input, context) => {
      for (const [key, data] of context?.previous ?? []) queryClient.setQueryData(key, data)
    },
    onSuccess: (result, input) => {
      const { title, description } = bulkResultMessage(input, result)
      if (result.affected.length === 0) toast.warning(title, { description })
      else toast.success(title, { description })
      onSuccess?.()
    },
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: listsKey }),
        queryClient.invalidateQueries({ queryKey: queryKeys.applications.details() }),
        // Counts by stage (KPI strip, Insights).
        queryClient.invalidateQueries({ queryKey: queryKeys.campaigns.detail(campaignId) }),
      ]),
  })
}

export type ExportParams = QueryParams<"exportCampaignApplications">

/** Save a blob as a file through a temporary link. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = filename
  link.rel = "noopener"
  document.body.appendChild(link)
  link.click()
  link.remove()
  // Revoke after the click has been handled.
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

/** The CSV's file name. The server's Content-Disposition is not readable cross-origin (CORS). */
export function exportFilename(campaignId: string): string {
  return `applications_${campaignId}.csv`
}

/**
 * Export CSV: the selected applications (`ids`), or every application matching
 * the filters, in the table's sort.
 */
export function useExportApplications(campaignId: string) {
  return useMutation<number, Error, ExportParams>({
    mutationFn: async (query) => {
      const blob = await api
        .GET("/campaigns/{campaign_id}/applications/export", {
          params: { path: { campaign_id: campaignId }, query },
          parseAs: "blob",
        })
        .then(unwrap)
      downloadBlob(blob, exportFilename(campaignId))
      return query.ids?.length ?? 0
    },
    onSuccess: (count) => {
      toast.success(count ? `Exported ${plural(count, "candidate")}` : "Exported the filtered candidates")
    },
  })
}
