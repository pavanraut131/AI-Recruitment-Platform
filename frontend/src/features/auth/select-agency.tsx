"use client"

import { useQueryClient } from "@tanstack/react-query"
import { ChevronRightIcon, Loader2Icon, RotateCwIcon } from "lucide-react"
import { useRouter } from "next/navigation"
import { useEffect, useState } from "react"

import { AuthHeader } from "@/components/auth/auth-layout"
import { FormAlert } from "@/components/auth/form-alert"
import { Button } from "@/components/ui/button"
import { ROLE_LABELS } from "@/lib/auth/roles"
import { isUnauthorized, startSession, useMe, useSignOut } from "@/lib/auth/session"

import { authError, type AuthError } from "./errors"
import { LinkLoading } from "./link-states"
import { useSetActiveAgency } from "./queries"

/**
 * `/select-agency?next=…` (EPIC-02 §4.3, §9.1): a signed-in user whose session
 * has no active agency (several agencies and none used yet, or access to the
 * last one ended) chooses one, then goes on to `next`. Someone with no agency
 * left is told so, and can sign out. A signed-out visitor is sent to the login
 * page by the session watcher.
 */
export function SelectAgency({ next }: { next: string }) {
  const router = useRouter()
  const queryClient = useQueryClient()
  const me = useMe()
  const choose = useSetActiveAgency()
  const signOut = useSignOut()
  const [choosing, setChoosing] = useState<string | null>(null)
  const [failure, setFailure] = useState<AuthError | null>(null)

  // Nothing to choose any more (chosen in another tab, or opened by hand).
  const hasAgency = !!me.data?.active_agency
  useEffect(() => {
    if (hasAgency) router.replace(next)
  }, [hasAgency, next, router])

  if (me.isError && !isUnauthorized(me.error)) {
    const { title, description } = authError(me.error)
    return (
      <div className="flex flex-col gap-6">
        <AuthHeader title={title}>{description && <p>{description}</p>}</AuthHeader>
        <Button variant="outline" onClick={() => void me.refetch()} disabled={me.isFetching}>
          <RotateCwIcon data-icon="inline-start" />
          Try again
        </Button>
      </div>
    )
  }
  if (!me.data || hasAgency) return <LinkLoading label="Loading your agencies" />

  const { user, memberships } = me.data
  const signOutLine = (
    <p className="text-dense text-muted-foreground">
      Signed in as {user.email}.{" "}
      <button type="button" className="text-link" onClick={() => signOut.mutate()} disabled={signOut.isPending}>
        Sign out
      </button>
    </p>
  )

  if (memberships.length === 0) {
    return (
      <div className="flex flex-col gap-6">
        <AuthHeader title="No agency access">
          <p>
            Your account isn&apos;t a member of any agency right now. Ask an owner or admin of your agency to invite you
            again.
          </p>
        </AuthHeader>
        {signOutLine}
      </div>
    )
  }

  function select(agencyId: string) {
    setFailure(null)
    setChoosing(agencyId)
    choose.mutate(agencyId, {
      onSuccess: (updated) => {
        // Another agency's data must never show: start from an empty cache.
        startSession(queryClient, updated)
        router.replace(next)
      },
      onError: (cause) => {
        setChoosing(null)
        setFailure(authError(cause))
      },
    })
  }

  return (
    <div className="flex flex-col gap-6">
      <AuthHeader title="Choose an agency">
        <p>Choose the agency to work in.</p>
      </AuthHeader>
      <ul aria-label="Your agencies" className="flex flex-col border-t">
        {memberships.map(({ agency, role }) => (
          <li key={agency.id} className="border-b">
            <button
              type="button"
              onClick={() => select(agency.id)}
              disabled={choosing !== null}
              className="flex w-full items-center gap-3 rounded-md px-2 py-3 text-left transition-colors hover:bg-muted disabled:opacity-60"
            >
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="truncate font-medium">{agency.name}</span>
                <span className="text-dense text-muted-foreground">{ROLE_LABELS[role]}</span>
              </span>
              {choosing === agency.id ? (
                <Loader2Icon className="size-4 animate-spin text-muted-foreground motion-reduce:animate-none" aria-hidden="true" />
              ) : (
                <ChevronRightIcon className="size-4 text-muted-foreground" aria-hidden="true" />
              )}
            </button>
          </li>
        ))}
      </ul>
      {failure && <FormAlert title={failure.title}>{failure.description}</FormAlert>}
      {signOutLine}
    </div>
  )
}
