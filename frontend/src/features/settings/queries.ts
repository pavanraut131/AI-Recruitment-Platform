"use client"

import { keepPreviousData, queryOptions, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query"

import { api, unwrap, type Schema } from "@/lib/api/client"
import { rememberCsrfToken } from "@/lib/api/csrf"
import { queryKeys } from "@/lib/api/query-keys"

/*
 * Settings' server state (EPIC-02 §8.2): the signed-in user's profile and
 * password (`/me`), the agency (`/agency`) and the team (`/team/members`,
 * `/team/invitations`). Forms and dialogs show their own errors
 * (`errorToast: false`); one-click actions in the team table say what
 * happened in a toast themselves.
 */

export type Me = Schema<"Me">
export type Agency = Schema<"Agency">
export type Member = Schema<"Member">
export type Invitation = Schema<"Invitation">
export type Role = Schema<"Role">

const QUIET = { errorToast: false } as const

/* ── Profile ─────────────────────────────────────────────── */

/** `PATCH /me`: the new name shows at once wherever the signed-in user is shown. */
export function useUpdateProfile() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Schema<"MeUpdate">) => api.PATCH("/me", { body }).then(unwrap),
    meta: QUIET,
    onSuccess: (me) => {
      rememberCsrfToken(me.csrf_token)
      queryClient.setQueryData(queryKeys.me.all, me)
      // Team lists show names too.
      void queryClient.invalidateQueries({ queryKey: queryKeys.team.all })
    },
  })
}

/**
 * `POST /me/password`: every other session ends and this one continues with
 * new cookies, so `/me` is fetched again for the new CSRF token.
 */
export function useChangePassword() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (body: Schema<"PasswordChange">) => {
      await api.POST("/me/password", { body }).then(unwrap)
    },
    meta: QUIET,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.me.all }),
  })
}

/* ── Agency ──────────────────────────────────────────────── */

export function agencyQuery() {
  return queryOptions({
    queryKey: queryKeys.agency.all,
    queryFn: ({ signal }) => api.GET("/agency", { signal }).then(unwrap),
  })
}

export function useAgency() {
  return useQuery(agencyQuery())
}

/** `PATCH /agency` (`agency:manage`): the new name, also in the switcher (`/me` again). */
export function useUpdateAgency() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Schema<"AgencyUpdate">) => api.PATCH("/agency", { body }).then(unwrap),
    meta: QUIET,
    onSuccess: (agency) => {
      queryClient.setQueryData(queryKeys.agency.all, agency)
      void queryClient.invalidateQueries({ queryKey: queryKeys.me.all })
    },
  })
}

/* ── Team ────────────────────────────────────────────────── */

/**
 * Members per page: the API's largest, so a team of any usual size is one
 * page, and the "last owner" guard rail can be explained from the list.
 */
export const MEMBERS_PAGE_SIZE = 100
export const INVITATIONS_PAGE_SIZE = 20

export function membersQuery(page: number) {
  const params = { page, page_size: MEMBERS_PAGE_SIZE }
  return queryOptions({
    queryKey: queryKeys.team.members(params),
    queryFn: ({ signal }) => api.GET("/team/members", { params: { query: params }, signal }).then(unwrap),
  })
}

export function useMembers(page: number) {
  return useQuery({ ...membersQuery(page), placeholderData: keepPreviousData })
}

export function invitationsQuery(page: number) {
  const params = { page, page_size: INVITATIONS_PAGE_SIZE }
  return queryOptions({
    queryKey: queryKeys.team.invitations(params),
    queryFn: ({ signal }) => api.GET("/team/invitations", { params: { query: params }, signal }).then(unwrap),
  })
}

export function useInvitations(page: number) {
  return useQuery({ ...invitationsQuery(page), placeholderData: keepPreviousData })
}

function invalidateTeam(queryClient: QueryClient) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.team.all })
}

/** `PATCH /team/members/{id}`: role and client access. */
export function useUpdateMember() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ memberId, body }: { memberId: string; body: Schema<"MemberUpdate"> }) =>
      api
        .PATCH("/team/members/{membership_id}", { params: { path: { membership_id: memberId } }, body })
        .then(unwrap),
    meta: QUIET,
    onSuccess: () => invalidateTeam(queryClient),
  })
}

/** `POST /team/members/{id}/deactivate` or `/reactivate`. */
export function useSetMemberStatus() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ memberId, active }: { memberId: string; active: boolean }) => {
      const path = { membership_id: memberId }
      return (
        active
          ? api.POST("/team/members/{membership_id}/reactivate", { params: { path } })
          : api.POST("/team/members/{membership_id}/deactivate", { params: { path } })
      ).then(unwrap)
    },
    meta: QUIET,
    onSuccess: () => invalidateTeam(queryClient),
  })
}

/** `POST /team/invitations`: the email goes out with a 7-day link. */
export function useInvite() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Schema<"InvitationCreate">) => api.POST("/team/invitations", { body }).then(unwrap),
    meta: QUIET,
    onSuccess: () => invalidateTeam(queryClient),
  })
}

/** `POST /team/invitations/{id}/resend`: a new link and a new 7-day expiry. */
export function useResendInvitation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (invitationId: string) =>
      api
        .POST("/team/invitations/{invitation_id}/resend", { params: { path: { invitation_id: invitationId } } })
        .then(unwrap),
    meta: QUIET,
    onSuccess: () => invalidateTeam(queryClient),
  })
}

/** `DELETE /team/invitations/{id}`: the link stops working. */
export function useRevokeInvitation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (invitationId: string) => {
      await api
        .DELETE("/team/invitations/{invitation_id}", { params: { path: { invitation_id: invitationId } } })
        .then(unwrap)
    },
    meta: QUIET,
    onSuccess: () => invalidateTeam(queryClient),
  })
}
