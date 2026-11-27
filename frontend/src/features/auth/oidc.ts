"use client"

import { queryOptions, useMutation, useQuery } from "@tanstack/react-query"

import { api, unwrap, type Schema } from "@/lib/api/client"
import { queryKeys } from "@/lib/api/query-keys"

/*
 * Sign in with Google and Microsoft (EPIC-02 §4.3, E2-08): the providers this
 * deployment offers, starting the flow, and what the login page says when the
 * provider's callback comes back with an `error`.
 */

export type OidcProvider = Schema<"OidcProvider">
export type AuthProvider = Schema<"AuthProvider">
export type OidcLoginError = Schema<"OidcLoginError">

/** A provider's name when the API has not said it (the callback's `provider` on `/login`). */
export const PROVIDER_NAMES: Record<OidcProvider, string> = { google: "Google", microsoft: "Microsoft" }

/** `provider` from a URL, as a name ("Google"), or `null` for anything else. */
export function providerName(provider: string | null | undefined): string | null {
  return provider && Object.prototype.hasOwnProperty.call(PROVIDER_NAMES, provider)
    ? PROVIDER_NAMES[provider as OidcProvider]
    : null
}

/**
 * `GET /auth/providers`: the configured providers, Google first; empty when
 * none is (the development and E2E stacks), and then no button shows. The
 * list changes only with the API's configuration, so it is kept a while.
 */
export function authProvidersQuery() {
  return queryOptions({
    queryKey: queryKeys.authProviders.all,
    queryFn: ({ signal }) => api.GET("/auth/providers", { signal }).then(unwrap),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    meta: { errorToast: false },
  })
}

export function useAuthProviders() {
  return useQuery(authProvidersQuery())
}

export type OidcStartInput = { provider: OidcProvider; next: string; invitationToken?: string }

/**
 * `POST /auth/oidc/{provider}/start` (with the CSRF header, like every unsafe
 * request): sets the flow's cookie and answers the provider's
 * `authorization_url`, which the page then opens.
 */
export function useStartOidcLogin() {
  return useMutation<Schema<"OidcStart">, Error, OidcStartInput>({
    mutationFn: ({ provider, next, invitationToken }) =>
      api
        .POST("/auth/oidc/{provider}/start", {
          params: { path: { provider } },
          body: { next, invitation_token: invitationToken ?? null },
        })
        .then(unwrap),
    meta: { errorToast: false },
  })
}

/** What the login page says about a failed Google or Microsoft sign-in. */
export type OidcNotice = { tone: "error" | "info"; title: string; description: string }

/**
 * The words for each `OidcLoginError` the callback can send back to
 * `/login?error=…&provider=…` (docs/design/README.md, voice: what happened,
 * then what to do). None says more than the person who signed in at the
 * provider may know. An unknown code gets a general message.
 */
export function oidcErrorNotice(error: string, provider: string | null | undefined): OidcNotice {
  const name = providerName(provider)
  const signIn = name ? `Sign-in with ${name}` : "Signing in"
  const notices: Record<OidcLoginError, OidcNotice> = {
    no_account: {
      tone: "error",
      title: "There is no HireFlow AI account for this address",
      description: "Ask your agency to invite you.",
    },
    no_access: {
      tone: "error",
      title: "Your account has no active agency access",
      description: "Ask an owner or admin of your agency to give you access again.",
    },
    email_not_verified: {
      tone: "error",
      title: `${name ?? "The provider"} hasn't confirmed this email address`,
      description: "Sign in with your email and password instead, or ask your agency to invite you.",
    },
    invitation_invalid: {
      tone: "error",
      title: "This invitation can't be used any more",
      description: "It has expired, or was replaced or already used. Ask for a new invitation.",
    },
    invitation_mismatch: {
      tone: "error",
      title: `This ${name ?? "sign-in"} account belongs to another address`,
      description:
        "The invitation is for a different email address. Use the account for that address, or ask for an invitation to yours.",
    },
    account_exists: {
      tone: "error",
      title: "You already have an account for this address",
      description: "Sign in with your email and password, then open the invitation link again to join.",
    },
    already_member: {
      tone: "info",
      title: "You're already a member of this agency",
      description: `Sign in to open it${name ? `, with ${name} or your password` : ""}.`,
    },
    cancelled: {
      tone: "info",
      title: `${signIn} was cancelled`,
      description: "Try again, or sign in with your email and password.",
    },
    expired: {
      tone: "error",
      title: `${signIn} took too long`,
      description: "Start again from this page, and finish within 10 minutes in the same browser.",
    },
    oidc_failed: {
      tone: "error",
      title: `${signIn} didn't work`,
      description: "Try again. If it keeps failing, sign in with your email and password.",
    },
    rate_limited: {
      tone: "error",
      title: "Too many sign-in attempts from this network",
      description: "Try again in a few minutes.",
    },
  }
  return Object.prototype.hasOwnProperty.call(notices, error)
    ? notices[error as OidcLoginError]
    : { tone: "error", title: "Signing in didn't work", description: "Try again, or sign in with your email and password." }
}
