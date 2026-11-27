"use client"

import { queryOptions, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query"

import { api, isApiProblem, unwrap, type Schema } from "@/lib/api/client"
import { forgetCsrfToken, rememberCsrfToken } from "@/lib/api/csrf"
import { queryKeys } from "@/lib/api/query-keys"

import { navigateTo } from "./navigate"
import { LOGIN_PATH } from "./paths"

/*
 * The signed-in user on the client (EPIC-02 §9.4). `GET /me` is the only
 * source of truth: a session cookie is HttpOnly and proves nothing to the
 * page. The auth gate loads it before the app shell renders; screens inside
 * the shell read it with `useSignedInUser`.
 */

export type Me = Schema<"Me">

/** Whether an error is the API saying there is no valid session. */
export function isUnauthorized(error: unknown): boolean {
  return isApiProblem(error) && error.status === 401
}

export function meQueryOptions() {
  return queryOptions({
    queryKey: queryKeys.me.all,
    queryFn: async ({ signal }) => {
      const me = await api.GET("/me", { signal }).then(unwrap)
      rememberCsrfToken(me.csrf_token)
      return me
    },
    // A 401 is an answer (signed out), never retried (`shouldRetry`) or toasted.
    meta: { errorToast: false },
  })
}

/** `GET /me`: loads (and refreshes) the signed-in user. The auth gate and the sign-in pages use it. */
export function useMe() {
  return useQuery(meQueryOptions())
}

/**
 * The signed-in user the auth gate loaded, for screens inside the app shell
 * (the user menu, Home's greeting). It never requests `/me` itself.
 */
export function useSignedInUser(): Me | undefined {
  return useQuery({ ...meQueryOptions(), enabled: false }).data
}

/**
 * A new session started (sign-in, an accepted invitation, another agency):
 * nothing cached for the previous one survives (spec §9.4, cache hygiene),
 * and the new user is cached for the gate.
 */
export function startSession(queryClient: QueryClient, me: Me): void {
  queryClient.clear()
  rememberCsrfToken(me.csrf_token)
  queryClient.setQueryData(queryKeys.me.all, me)
}

const ending = new WeakSet<QueryClient>()

/**
 * The session is over (sign-out, a 401): forget everything cached and load
 * `href` as a new page. Once per page: later calls (other requests failing
 * meanwhile) are ignored, so the first destination wins.
 */
export function endSession(queryClient: QueryClient, href: string): void {
  if (ending.has(queryClient)) return
  ending.add(queryClient)
  queryClient.clear()
  forgetCsrfToken()
  navigateTo(href)
}

/**
 * Sign out: `POST /auth/logout`, then {@link endSession} to the login page. A
 * 401 means the session had already ended, which is just as good.
 */
export function useSignOut() {
  const queryClient = useQueryClient()
  return useMutation<void, Error, void>({
    mutationFn: async () => {
      try {
        await api.POST("/auth/logout")
      } catch (error) {
        if (!isUnauthorized(error)) throw error
      }
    },
    onSuccess: () => endSession(queryClient, LOGIN_PATH),
  })
}
