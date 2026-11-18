import { queryOptions, useMutation, useQueryClient, type QueryClient } from "@tanstack/react-query"

import { api, unwrap, type Schema } from "@/lib/api/client"
import { queryKeys } from "@/lib/api/query-keys"
import type { Application, Stage } from "@/lib/applications"

/*
 * One application: its query and its edits (stage, notes), shared by the
 * review panel (`features/applications`) and the Person profile
 * (`features/people`). An edit keeps every cached copy of the application in
 * step (ADR 0007): its detail, the campaign's table pages and the Person's
 * applications.
 */

type ApplicationPage = Schema<"ApplicationPage">
type Person = Schema<"Person">

export function applicationQueryOptions(applicationId: string) {
  return queryOptions({
    queryKey: queryKeys.applications.detail(applicationId),
    queryFn: ({ signal }) =>
      api
        .GET("/applications/{application_id}", { params: { path: { application_id: applicationId } }, signal })
        .then(unwrap),
  })
}

/** Where an application is cached besides its own detail. */
export type ApplicationRef = {
  applicationId: string
  campaignId: string
  /** The Person, to update their profile's list of applications (when known). */
  personId?: string
}

type Snapshot = [readonly unknown[], unknown][]

/** Apply `patch` to every cached copy of the application; returns the previous data, to roll back. */
function patchCachedApplication(
  queryClient: QueryClient,
  { applicationId, campaignId, personId }: ApplicationRef,
  patch: Partial<Application>,
): Snapshot {
  const snapshot: Snapshot = []
  const remember = (key: readonly unknown[]) => snapshot.push([key, queryClient.getQueryData(key)])

  const detailKey = queryKeys.applications.detail(applicationId)
  remember(detailKey)
  queryClient.setQueryData<Application>(detailKey, (application) => (application ? { ...application, ...patch } : application))

  for (const [key] of queryClient.getQueriesData<ApplicationPage>({
    queryKey: queryKeys.applications.campaignLists(campaignId),
  })) {
    remember(key)
    queryClient.setQueryData<ApplicationPage>(key, (page) => {
      if (!page?.items.some((item) => item.id === applicationId)) return page
      // Only the edited item is a new object: the table's memoised rows re-render that row only.
      return { ...page, items: page.items.map((item) => (item.id === applicationId ? { ...item, ...patch } : item)) }
    })
  }

  if (personId) {
    const personKey = queryKeys.people.detail(personId)
    remember(personKey)
    const { pipeline_stage, updated_at } = patch
    queryClient.setQueryData<Person>(personKey, (person) =>
      person
        ? {
            ...person,
            applications: person.applications.map((item) =>
              item.id === applicationId
                ? {
                    ...item,
                    ...(pipeline_stage !== undefined && { pipeline_stage }),
                    ...(updated_at !== undefined && { updated_at }),
                  }
                : item,
            ),
          }
        : person,
    )
  }
  return snapshot
}

function restore(queryClient: QueryClient, snapshot: Snapshot) {
  for (const [key, data] of snapshot) queryClient.setQueryData(key, data)
}

/** Copy the server's answer into the caches (fields the server set, like `updated_at`). */
function storeApplication(queryClient: QueryClient, ref: ApplicationRef, application: Application) {
  patchCachedApplication(queryClient, ref, application)
  // Also when the detail was not cached yet (the panel falls back to it when its row leaves the page).
  queryClient.setQueryData(queryKeys.applications.detail(ref.applicationId), application)
}

function patchApplication(applicationId: string, body: Schema<"ApplicationUpdate">) {
  return api
    .PATCH("/applications/{application_id}", { params: { path: { application_id: applicationId } }, body })
    .then(unwrap)
}

export type StageChange = ApplicationRef & { stage: Stage | null }

/**
 * Move one application to a stage (or back to unreviewed, `null`),
 * optimistically: the panel, the table row and the Person's applications show
 * the new stage at once and are restored if the request fails (the global
 * mutation handler toasts the problem). Counts by stage are refreshed after.
 */
export function useUpdateApplicationStage() {
  const queryClient = useQueryClient()
  return useMutation<Application, Error, StageChange, { snapshot: Snapshot }>({
    mutationFn: ({ applicationId, stage }) => patchApplication(applicationId, { pipeline_stage: stage }),
    onMutate: async (change) => {
      const { applicationId, campaignId, personId, stage } = change
      // A refetch in flight would overwrite the optimistic stage with the old one.
      await Promise.all([
        queryClient.cancelQueries({ queryKey: queryKeys.applications.detail(applicationId) }),
        queryClient.cancelQueries({ queryKey: queryKeys.applications.campaignLists(campaignId) }),
        personId ? queryClient.cancelQueries({ queryKey: queryKeys.people.detail(personId) }) : undefined,
      ])
      return { snapshot: patchCachedApplication(queryClient, change, { pipeline_stage: stage }) }
    },
    onError: (_error, _change, context) => {
      if (context) restore(queryClient, context.snapshot)
    },
    onSuccess: (application, change) => storeApplication(queryClient, change, application),
    onSettled: (_data, _error, { applicationId, campaignId, personId }) =>
      Promise.all([
        // The table's stage filter may now include or exclude the row.
        queryClient.invalidateQueries({ queryKey: queryKeys.applications.campaignLists(campaignId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.applications.detail(applicationId) }),
        // Counts by stage: the campaign's stats (below its detail key), Home and workspace stats.
        queryClient.invalidateQueries({ queryKey: queryKeys.campaigns.detail(campaignId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.home.all }),
        queryClient.invalidateQueries({ queryKey: queryKeys.stats.all }),
        personId ? queryClient.invalidateQueries({ queryKey: queryKeys.people.detail(personId) }) : undefined,
      ]),
  })
}

/**
 * Save an application's notes (empty → `null`) and store the result in the
 * caches. A plain function rather than a mutation hook: the notes editor also
 * calls it while unmounting (closing the panel, switching candidates), when a
 * hook's callbacks would no longer run. Failures throw an `ApiProblem`; the
 * editor shows them.
 */
export async function saveApplicationNotes(queryClient: QueryClient, ref: ApplicationRef, notes: string): Promise<void> {
  const application = await patchApplication(ref.applicationId, { notes: notes.trim() === "" ? null : notes })
  storeApplication(queryClient, ref, application)
}
