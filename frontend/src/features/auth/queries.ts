"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { api, unwrap, type Schema } from "@/lib/api/client"
import { forgetCsrfToken } from "@/lib/api/csrf"
import { queryKeys } from "@/lib/api/query-keys"
import { isUnauthorized, type Me } from "@/lib/auth/session"

/*
 * The sign-in pages' requests (EPIC-02 §4.3, §8.2). Every one shows its
 * errors in the form (`errorToast: false`), never as a toast.
 */

export type InvitationPreview = Schema<"InvitationPreview">

const QUIET = { errorToast: false } as const

/** `POST /auth/login`: a new session (new cookies) and the signed-in user. */
export function useLogin() {
  return useMutation<Me, Error, Schema<"LoginRequest">>({
    mutationFn: (body) => api.POST("/auth/login", { body }).then(unwrap),
    meta: QUIET,
  })
}

/** `POST /auth/password-reset`: always the same 202, whether or not the account exists. */
export function useRequestPasswordReset() {
  return useMutation<void, Error, Schema<"PasswordResetRequest">>({
    mutationFn: async (body) => {
      await api.POST("/auth/password-reset", { body }).then(unwrap)
    },
    meta: QUIET,
  })
}

/** `POST /auth/password-reset/confirm`: sets the password and ends every session of the account. */
export function useConfirmPasswordReset() {
  return useMutation<void, Error, Schema<"PasswordResetConfirm">>({
    mutationFn: async (body) => {
      await api.POST("/auth/password-reset/confirm", { body }).then(unwrap)
    },
    meta: QUIET,
  })
}

/**
 * `POST /auth/invitations/preview`: what an invitation link offers. A read,
 * so a query, but one that is never repeated by itself: the endpoint is rate
 * limited and the answer does not change while the page is open.
 */
export function useInvitationPreview(token: string | null | undefined) {
  return useQuery({
    // The token stays in memory (the key is never sent or stored anywhere).
    queryKey: ["invitation-preview", token],
    queryFn: ({ signal }) => api.POST("/auth/invitations/preview", { body: { token: token! }, signal }).then(unwrap),
    enabled: !!token,
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    meta: QUIET,
  })
}

/** `POST /auth/invitations/accept`: joins the agency (and creates the account) in a new session. */
export function useAcceptInvitation() {
  return useMutation<Me, Error, Schema<"InvitationAccept">>({
    mutationFn: (body) => api.POST("/auth/invitations/accept", { body }).then(unwrap),
    meta: QUIET,
  })
}

/** `PUT /me/active-agency`: work in another of the user's agencies. */
export function useSetActiveAgency() {
  return useMutation<Me, Error, string>({
    mutationFn: (agencyId) => api.PUT("/me/active-agency", { body: { agency_id: agencyId } }).then(unwrap),
    meta: QUIET,
  })
}

/**
 * Sign out but stay on this page (accepting an invitation for another
 * address): `POST /auth/logout`, then `/me` is asked again (a 401 now). A 401
 * from the logout means the session had ended already.
 */
export function useSignOutHere() {
  const queryClient = useQueryClient()
  return useMutation<void, Error, void>({
    mutationFn: async () => {
      try {
        await api.POST("/auth/logout")
      } catch (error) {
        if (!isUnauthorized(error)) throw error
      }
    },
    onSuccess: async () => {
      forgetCsrfToken()
      await queryClient.resetQueries({ queryKey: queryKeys.me.all })
    },
  })
}
