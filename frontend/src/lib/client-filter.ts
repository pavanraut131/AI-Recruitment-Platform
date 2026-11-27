"use client"

import { parseAsInteger, parseAsString, useQueryStates } from "nuqs"
import { useCallback, useSyncExternalStore } from "react"

import { useSignedInUser } from "@/lib/auth/session"
import { usePickerClients } from "@/lib/query/clients"

/*
 * The client Home and the campaign list show (EPIC-02 §9.2, §9.4): "All
 * clients" or one of them.
 *
 * - It lives in the URL (`?client=<id>`, ADR 0007) on the pages it filters,
 *   Home and the campaign list, so a filtered view can be shared and Back
 *   undoes a change.
 * - The last choice is remembered per agency in `localStorage` and is the
 *   default when the URL has no `client`. "All clients" forgets it.
 * - The shell's switcher shows and changes it from every page; Talent and the
 *   other screens ignore it (the talent pool is agency-wide, §5.3).
 *
 * It only narrows what the API returns: the API intersects `client_id` with
 * the clients the user may see, so a stale or foreign id shows nothing.
 */

/** The pages whose content follows the client choice (and carry it in the URL). */
export const CLIENT_FILTERED_PATHS = ["/", "/campaigns"] as const

export function isClientFilteredPath(pathname: string): boolean {
  return (CLIENT_FILTERED_PATHS as readonly string[]).includes(pathname)
}

/** The URL state: the client, and the list's page (a new client starts from the first page). */
export const clientFilterSearchParams = {
  client: parseAsString,
  page: parseAsInteger,
}

const STORAGE_PREFIX = "hireflow.client."
const listeners = new Set<() => void>()

function storageKey(agencyId: string): string {
  return `${STORAGE_PREFIX}${agencyId}`
}

/** The client remembered for an agency, or `null` (none, or storage unavailable). */
export function readRememberedClient(agencyId: string | null | undefined): string | null {
  if (!agencyId) return null
  try {
    return window.localStorage.getItem(storageKey(agencyId))
  } catch {
    return null
  }
}

/** Remembers `clientId` as the agency's default (`null`: all clients). Storage failures are ignored. */
export function rememberClient(agencyId: string | null | undefined, clientId: string | null): void {
  if (!agencyId) return
  try {
    if (clientId) window.localStorage.setItem(storageKey(agencyId), clientId)
    else window.localStorage.removeItem(storageKey(agencyId))
  } catch {
    // Private windows and blocked storage: the choice lasts as long as the URL.
  }
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  // Another tab changed it.
  window.addEventListener("storage", listener)
  return () => {
    listeners.delete(listener)
    window.removeEventListener("storage", listener)
  }
}

function useRememberedClient(agencyId: string | null | undefined): string | null {
  return useSyncExternalStore(
    subscribe,
    () => readRememberedClient(agencyId),
    () => null,
  )
}

export type ClientFilter = {
  /** The chosen client's id, or `null` for all clients. */
  clientId: string | null
  /** Choose a client (`null`: all clients). */
  setClientId: (clientId: string | null) => void
}

/**
 * The client choice. `fromUrl`: this page carries it in the URL (Home, the
 * campaign list, the new-campaign form); elsewhere only the remembered choice
 * counts and choosing does not touch the URL.
 *
 * A remembered client the user can no longer see (archived and gone from the
 * list, or access removed) is ignored once the client list has loaded.
 */
export function useClientFilter({ fromUrl }: { fromUrl: boolean }): ClientFilter {
  const me = useSignedInUser()
  const agencyId = me?.active_agency?.id
  const remembered = useRememberedClient(agencyId)
  const clients = usePickerClients()
  const [{ client: param }, setParams] = useQueryStates(clientFilterSearchParams)

  const known = clients.data?.items
  const rememberedValid = remembered && (known === undefined || known.some((client) => client.id === remembered))
  const clientId = (fromUrl ? param : null) ?? (rememberedValid ? remembered : null)

  const setClientId = useCallback(
    (next: string | null) => {
      rememberClient(agencyId, next)
      if (fromUrl) void setParams({ client: next, page: null })
    },
    [agencyId, fromUrl, setParams],
  )

  return { clientId, setClientId }
}

/** The `client_id` query parameter for a choice: one client, or none for all. */
export function clientIdParam(clientId: string | null): { client_id?: string[] } {
  return clientId ? { client_id: [clientId] } : {}
}

/** "All clients", or "All your clients" for someone limited to some of them (§5.3). */
export function allClientsLabel(limited: boolean): string {
  return limited ? "All your clients" : "All clients"
}

/** Whether the signed-in user sees only some of the agency's clients. */
export function useLimitedToSomeClients(): boolean {
  return useSignedInUser()?.client_scope?.all_clients === false
}
