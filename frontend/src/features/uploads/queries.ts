import {
  keepPreviousData,
  queryOptions,
  useMutation,
  useMutationState,
  useQuery,
  useQueryClient,
  type Mutation,
  type QueryClient,
} from "@tanstack/react-query"
import { useCallback } from "react"
import { toast } from "sonner"

import { api, unwrap, type Schema } from "@/lib/api/client"
import { queryKeys, type ResumeListParams } from "@/lib/api/query-keys"

import { postUpload, type UploadResult } from "./upload-request"
import { uploadProgress } from "./upload-progress"

export type Resume = Schema<"Resume">
export type ResumePage = Schema<"ResumePage">
export type RetryResult = Schema<"RetryResult">

/** Resumes per page of the Uploads list. */
export const RESUME_PAGE_SIZE = 50

/** One page of the campaign's resumes, newest upload first. */
export function resumesListOptions(campaignId: string, params: ResumeListParams) {
  return queryOptions({
    queryKey: queryKeys.resumes.list(campaignId, params),
    queryFn: ({ signal }) =>
      api
        .GET("/campaigns/{campaign_id}/resumes", {
          params: { path: { campaign_id: campaignId }, query: params },
          signal,
        })
        .then(unwrap),
  })
}

/** A page of the Uploads list. Kept live by the workspace's `useCampaignEvents` (cache patches). */
export function useCampaignResumes(campaignId: string, page: number) {
  return useQuery({
    ...resumesListOptions(campaignId, { page, page_size: RESUME_PAGE_SIZE }),
    // Keep the current rows on screen while the next page loads.
    placeholderData: keepPreviousData,
  })
}

/** The campaign's resume counts (all pages): `failed` includes quota-exceeded ones. */
export function useResumeCounts(campaignId: string) {
  return useQuery({
    queryKey: queryKeys.campaigns.detail(campaignId),
    queryFn: ({ signal }) =>
      api.GET("/campaigns/{campaign_id}", { params: { path: { campaign_id: campaignId } }, signal }).then(unwrap),
    select: (campaign) => campaign.counts,
  })
}

/* ── Uploads ────────────────────────────────────────────────────────────── */

/** What one upload request sends: up to `MAX_FILES_PER_BATCH` files. */
export type UploadBatchInput = { batchId: string; files: File[] }

/** The mutation key of a campaign's upload batches (a mutation key, so not in `queryKeys`). */
export function uploadMutationKey(campaignId: string) {
  return ["uploads", campaignId] as const
}

/** Finished batches stay listed this long after the Uploads tab stops showing them. */
const UPLOAD_GC_MS = 30 * 60_000

/** After an upload, refetch what it changed. */
function invalidateAfterUpload(queryClient: QueryClient, campaignId: string): void {
  void queryClient.invalidateQueries({ queryKey: queryKeys.resumes.campaignLists(campaignId) })
  void queryClient.invalidateQueries({ queryKey: queryKeys.campaigns.detail(campaignId) })
  void queryClient.invalidateQueries({ queryKey: queryKeys.campaigns.lists() })
  void queryClient.invalidateQueries({ queryKey: queryKeys.home.all })
}

/**
 * Put newly accepted resumes at the top of the cached first pages, so they show
 * at once (the refetch that follows replaces the page). A resume the page
 * already holds (its first event arrived before the upload's response) is kept
 * as it is: the cached copy is the newer one.
 */
export function addAcceptedResumes(queryClient: QueryClient, campaignId: string, accepted: readonly Resume[]): void {
  if (accepted.length === 0) return
  for (const [key, page] of queryClient.getQueriesData<ResumePage>({
    queryKey: queryKeys.resumes.campaignLists(campaignId),
  })) {
    const params = (key[3] ?? {}) as ResumeListParams
    if (!page || (params.page ?? 1) !== 1 || params.status?.length) continue
    const known = new Set(page.items.map((resume) => resume.id))
    const fresh = accepted.filter((resume) => !known.has(resume.id))
    queryClient.setQueryData<ResumePage>(key, {
      ...page,
      items: [...fresh, ...page.items].slice(0, page.page_size),
      total: page.total + fresh.length,
    })
  }
}

/**
 * Upload a batch of resumes (`uploadResumes`). Every batch of a campaign runs
 * in one mutation `scope`, so batches are sent one at a time, in order; the
 * ones waiting are `isPaused`, shown as Queued. The batches live in the
 * mutation cache, so they keep going (and keep their result) when the Uploads
 * tab is unmounted. Errors are shown on the batch's rows, not toasted.
 */
export function useUploadResumes(campaignId: string) {
  const queryClient = useQueryClient()
  return useMutation<UploadResult, Error, UploadBatchInput>({
    mutationKey: uploadMutationKey(campaignId),
    scope: { id: `uploads:${campaignId}` },
    gcTime: UPLOAD_GC_MS,
    meta: { errorToast: false },
    mutationFn: ({ batchId, files }) =>
      postUpload(campaignId, files, {
        onProgress: (fraction) => uploadProgress.set(batchId, fraction),
      }).finally(() => uploadProgress.delete(batchId)),
    onSuccess: (result) => addAcceptedResumes(queryClient, campaignId, result.accepted),
    // Not awaited: the next batch need not wait for the refetch.
    onSettled: () => invalidateAfterUpload(queryClient, campaignId),
  })
}

/** One upload batch as the Uploads tab shows it. */
export type UploadBatch = {
  mutationId: number
  batchId: string
  files: File[]
  /** `queued`: waiting for an earlier batch; `uploading`: being sent. */
  phase: "queued" | "uploading" | "done" | "error"
  result: UploadResult | undefined
  error: Error | null
}

function toBatch(mutation: Mutation<unknown, Error, unknown>): UploadBatch {
  const { status, isPaused, variables, data, error } = mutation.state
  const input = variables as UploadBatchInput
  return {
    mutationId: mutation.mutationId,
    batchId: input.batchId,
    files: input.files,
    phase:
      status === "success" ? "done" : status === "error" ? "error" : status === "pending" && !isPaused ? "uploading" : "queued",
    result: data as UploadResult | undefined,
    error,
  }
}

/** The campaign's upload batches in the mutation cache, oldest first. */
export function useUploadBatches(campaignId: string): UploadBatch[] {
  return useMutationState({
    filters: { mutationKey: uploadMutationKey(campaignId) },
    select: (mutation) => toBatch(mutation as Mutation<unknown, Error, unknown>),
  })
}

/** Forget finished batches (the list's "Clear"), or the given ones. */
export function useForgetBatches(campaignId: string) {
  const queryClient = useQueryClient()
  return useCallback(
    (mutationIds?: readonly number[]) => {
      const cache = queryClient.getMutationCache()
      for (const mutation of cache.findAll({ mutationKey: uploadMutationKey(campaignId) })) {
        const settled = mutation.state.status === "success" || mutation.state.status === "error"
        if (mutationIds ? mutationIds.includes(mutation.mutationId) : settled) cache.remove(mutation)
      }
    },
    [campaignId, queryClient],
  )
}

/* ── Retry ──────────────────────────────────────────────────────────────── */

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`
}

/**
 * "Retry failed" (`retryFailedResumes`): the campaign's failed and quota-limited
 * resumes are queued again, each from the step it stopped at. The server sends
 * no event for that, so the retried rows are set to Queued here, then the
 * lists and counts are refetched.
 */
export function useRetryFailed(campaignId: string) {
  const queryClient = useQueryClient()
  const listsKey = queryKeys.resumes.campaignLists(campaignId)
  return useMutation<RetryResult, Error, void>({
    mutationFn: () =>
      api
        .POST("/campaigns/{campaign_id}/resumes/retry-failed", { params: { path: { campaign_id: campaignId } } })
        .then(unwrap),
    onSuccess: ({ retried }) => {
      const ids = new Set(retried)
      queryClient.setQueriesData<ResumePage>({ queryKey: listsKey }, (page) =>
        page
          ? {
              ...page,
              items: page.items.map((resume) =>
                ids.has(resume.id)
                  ? { ...resume, status: "pending", error_message: null, retry_count: 0 }
                  : resume,
              ),
            }
          : page,
      )
      if (retried.length === 0) toast.info("Nothing to retry", { description: "No resume has failed." })
      else toast.success(`Retrying ${plural(retried.length, "resume")}`)
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: listsKey })
      void queryClient.invalidateQueries({ queryKey: queryKeys.campaigns.detail(campaignId) })
    },
  })
}
