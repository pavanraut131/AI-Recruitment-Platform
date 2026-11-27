"use client"

import { Loader2Icon } from "lucide-react"
import { useState } from "react"

import { FormAlert } from "@/components/auth/form-alert"
import { Button } from "@/components/ui/button"
import { isApiProblem } from "@/lib/api/client"
import { navigateTo } from "@/lib/auth/navigate"

import { authError, type AuthError } from "./errors"
import { useAuthProviders, useStartOidcLogin, type AuthProvider } from "./oidc"
import { ProviderLogo } from "./provider-logo"

export type SignInProvidersProps = {
  /** Where to land after signing in (a path from `safeNextPath`). */
  next: string
  /** The invitation being accepted (`/accept-invite`): signing in with the provider also accepts it. */
  invitationToken?: string
  /** The invitation is no longer valid (a 410 when starting). */
  onInvitationGone?: (detail: string) => void
}

/** Why starting the flow failed, in the sign-in pages' words. */
function startError(error: unknown, provider: AuthProvider): AuthError {
  if (isApiProblem(error) && error.status === 502) {
    return {
      title: `${provider.name} can't be reached right now`,
      description: "Try again in a moment, or sign in with your email and password.",
    }
  }
  if (isApiProblem(error) && error.status === 404) {
    return {
      title: `Sign-in with ${provider.name} isn't available any more`,
      description: "Sign in with your email and password.",
    }
  }
  return authError(error)
}

/**
 * "Continue with Google" and "Continue with Microsoft" (EPIC-02 §4.3, §9.1;
 * E2-08), on `/login` and `/accept-invite`: one outline button per provider
 * the API offers (`GET /auth/providers`), above an "or" and the email form.
 * Nothing at all when none is configured (the development and E2E stacks), or
 * while the list loads or if it cannot be had: the email form still works.
 *
 * A click starts the flow (`POST /auth/oidc/{provider}/start` with `next`
 * and, on an invitation, its token) and opens the provider's page. The
 * provider sends the browser back to the API, which signs it in and opens
 * `next`, or opens `/login?error=…` (`oidcErrorNotice`). A 410 hands the
 * invitation page its "can't be used any more" state.
 */
export function SignInProviders({ next, invitationToken, onInvitationGone }: SignInProvidersProps) {
  const providers = useAuthProviders()
  const start = useStartOidcLogin()
  const [failure, setFailure] = useState<AuthError | null>(null)
  const [leaving, setLeaving] = useState<string | null>(null)

  const list = providers.data?.providers ?? []
  if (list.length === 0) return null

  function begin(provider: AuthProvider) {
    setFailure(null)
    start.mutate(
      { provider: provider.id, next, invitationToken },
      {
        onSuccess: ({ authorization_url }) => {
          setLeaving(provider.id)
          navigateTo(authorization_url)
        },
        onError: (cause) => {
          if (isApiProblem(cause) && cause.status === 410 && onInvitationGone) {
            onInvitationGone(cause.detail ?? "This invitation can't be used any more. Ask for a new invitation.")
            return
          }
          // A provider that is no longer configured disappears from the list.
          if (isApiProblem(cause) && cause.status === 404) void providers.refetch()
          setFailure(startError(cause, provider))
        },
      },
    )
  }

  const busy = start.isPending || leaving !== null

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-2" role="group" aria-label="Sign in with another account">
        {list.map((provider) => {
          const opening = (start.isPending && start.variables?.provider === provider.id) || leaving === provider.id
          return (
            <Button
              key={provider.id}
              type="button"
              variant="outline"
              size="lg"
              className="w-full"
              disabled={busy}
              onClick={() => begin(provider)}
            >
              {opening ? (
                <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />
              ) : (
                <ProviderLogo provider={provider.id} />
              )}
              {opening ? `Opening ${provider.name}…` : `Continue with ${provider.name}`}
            </Button>
          )
        })}
      </div>
      {failure && <FormAlert title={failure.title}>{failure.description}</FormAlert>}
      <div className="flex items-center gap-3 text-dense text-muted-foreground" aria-hidden="true">
        <span className="h-px flex-1 bg-border" />
        or
        <span className="h-px flex-1 bg-border" />
      </div>
    </div>
  )
}
