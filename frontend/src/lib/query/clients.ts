"use client"

import { keepPreviousData, queryOptions, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query"

import { api, unwrap, type Schema } from "@/lib/api/client"
import { queryKeys, type ClientListParams } from "@/lib/api/query-keys"

/*
 * The agency's clients (EPIC-02 §3.1, §9.3): every campaign is run for one.
 * `GET /clients` lists only the clients the user may see (a recruiter or
 * viewer limited to some clients sees those), so the pickers never offer
 * another. Shared by the shell's client switcher, the campaign forms and
 * Settings → Clients.
 */

export type Client = Schema<"Client">
export type ClientRef = Schema<"ClientRef">

/** The API's largest page: the pickers load the clients in one request. */
export const CLIENT_PICKER_PAGE_SIZE = 100

/** One page of clients, as Settings → Clients filters them (`status`, `q`). */
export function clientListQuery(params: ClientListParams) {
  return queryOptions({
    queryKey: queryKeys.clients.list(params),
    queryFn: ({ signal }) => api.GET("/clients", { params: { query: params }, signal }).then(unwrap),
  })
}

export function useClientList(params: ClientListParams) {
  return useQuery({ ...clientListQuery(params), placeholderData: keepPreviousData })
}

/**
 * Every client the user may see, active and archived, by name (one request of
 * up to {@link CLIENT_PICKER_PAGE_SIZE}). Pickers offer the active ones;
 * archived ones are kept to name a campaign's or a filter's client.
 */
export function pickerClientsQuery() {
  return clientListQuery({ page_size: CLIENT_PICKER_PAGE_SIZE })
}

export function usePickerClients() {
  return useQuery(pickerClientsQuery())
}

/** The clients a picker offers: the active ones, plus `keep` (the current choice) even when archived. */
export function pickableClients(clients: readonly Client[] | undefined, keep?: string | null): Client[] {
  return (clients ?? []).filter((client) => client.status === "active" || client.id === keep)
}

/** A client's name, or `undefined` when the list has not loaded or does not have it. */
export function clientName(clients: readonly Client[] | undefined, clientId: string | null | undefined): string | undefined {
  if (!clientId) return undefined
  return clients?.find((client) => client.id === clientId)?.name
}

/**
 * After a client is created or changed: every list of clients, and what shows
 * a client's name (campaigns, Home).
 */
function invalidateClients(queryClient: QueryClient, { renamed = false } = {}) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.clients.all })
  if (renamed) {
    void queryClient.invalidateQueries({ queryKey: queryKeys.campaigns.all })
    void queryClient.invalidateQueries({ queryKey: queryKeys.home.all })
  }
}

/** `POST /clients` (`client:manage`). The form shows its errors (409: the name is taken). */
export function useCreateClient() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Schema<"ClientCreate">) => api.POST("/clients", { body }).then(unwrap),
    meta: { errorToast: false },
    onSuccess: (client) => {
      // The pickers can select it at once, before the list is fetched again.
      queryClient.setQueryData(pickerClientsQuery().queryKey, (page) =>
        page && !page.items.some((item) => item.id === client.id)
          ? {
              ...page,
              items: [...page.items, client].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" })),
              total: page.total + 1,
            }
          : page,
      )
      invalidateClients(queryClient)
    },
  })
}

/** `PATCH /clients/{id}` (`client:manage`): rename, archive or restore. */
export function useUpdateClient() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ clientId, body }: { clientId: string; body: Schema<"ClientUpdate"> }) =>
      api.PATCH("/clients/{client_id}", { params: { path: { client_id: clientId } }, body }).then(unwrap),
    meta: { errorToast: false },
    onSuccess: (_client, { body }) => invalidateClients(queryClient, { renamed: body.name !== undefined }),
  })
}
