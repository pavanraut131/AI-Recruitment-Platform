"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useQueryClient } from "@tanstack/react-query"
import { Loader2Icon, RotateCwIcon } from "lucide-react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { toast } from "sonner"
import { z } from "zod"

import { AuthHeader } from "@/components/auth/auth-layout"
import { FormAlert } from "@/components/auth/form-alert"
import { PasswordInput } from "@/components/auth/password-input"
import { Button } from "@/components/ui/button"
import { Form, FormControl, FormDescription, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { isApiProblem } from "@/lib/api/client"
import { rememberCsrfToken } from "@/lib/api/csrf"
import { queryKeys } from "@/lib/api/query-keys"
import { PASSWORD_HINT, newPasswordSchema } from "@/lib/auth/password"
import { ROLE_IN_SENTENCE } from "@/lib/auth/roles"
import { isUnauthorized, startSession, useMe, type Me } from "@/lib/auth/session"

import { authError, showFieldErrors, type AuthError } from "./errors"
import { LinkLoading, LinkProblem } from "./link-states"
import { SignInProviders } from "./sign-in-providers"
import { useAcceptInvitation, useInvitationPreview, useLogin, useSignOutHere, type InvitationPreview } from "./queries"
import { useFragmentToken } from "./use-fragment-token"

const GONE_FALLBACK = "This invitation has expired or is no longer valid. Ask for a new invitation."

function sameAddress(a: string, b: string): boolean {
  return a.trim().toLowerCase() === b.trim().toLowerCase()
}

/**
 * `/accept-invite#token=…` (EPIC-02 §4.3, §9.1): join an agency from an
 * invitation email. The page previews the invitation (agency, who sent it,
 * role, address), then:
 *
 * - **a new person** chooses a display name and a password, or continues
 *   with Google or Microsoft when the API offers them, and the account is
 *   created;
 * - **someone with an account** signs in first, then joins with it;
 * - someone **signed in as another address** can sign out and continue.
 *
 * Joining starts a session in the agency and opens Home. An expired, used or
 * replaced invitation (410) says so and what to do.
 */
export function AcceptInvitation() {
  const token = useFragmentToken()
  const preview = useInvitationPreview(token)
  const [gone, setGone] = useState<string | null>(null)

  if (token === undefined) return <LinkLoading label="Reading the invitation" />
  if (token === null) {
    return (
      <LinkProblem title="This invitation link is incomplete" action={<GoToSignIn />}>
        <p>Open the link from your invitation email again. If it still doesn&apos;t work, ask for a new invitation.</p>
      </LinkProblem>
    )
  }
  const goneDetail =
    gone ?? (isApiProblem(preview.error) && preview.error.status === 410 ? (preview.error.detail ?? GONE_FALLBACK) : null)
  if (goneDetail !== null) {
    return (
      <LinkProblem title="This invitation can't be used any more" action={<GoToSignIn />}>
        <p>{goneDetail}</p>
      </LinkProblem>
    )
  }
  if (preview.isPending) return <LinkLoading label="Checking the invitation" />
  if (preview.isError) {
    const { title, description } = authError(preview.error)
    return (
      <LinkProblem
        title={title}
        action={
          <Button variant="outline" className="w-full" onClick={() => void preview.refetch()} disabled={preview.isFetching}>
            <RotateCwIcon data-icon="inline-start" />
            Try again
          </Button>
        }
      >
        {description && <p>{description}</p>}
      </LinkProblem>
    )
  }

  const invitation = preview.data
  return (
    <div className="flex flex-col gap-6">
      <AuthHeader title={`Join ${invitation.agency.name}`}>
        <p>
          {invitation.invited_by ? `${invitation.invited_by} invited you` : "You're invited"} to join{" "}
          <span className="font-medium text-foreground">{invitation.agency.name}</span> as{" "}
          {ROLE_IN_SENTENCE[invitation.role]}.
        </p>
      </AuthHeader>
      <InvitationActions invitation={invitation} token={token} onGone={setGone} />
    </div>
  )
}

function GoToSignIn() {
  return (
    <Button variant="outline" asChild className="w-full">
      <Link href="/login">Go to sign in</Link>
    </Button>
  )
}

type ActionsProps = { invitation: InvitationPreview; token: string; onGone: (detail: string) => void }

/** Who is looking decides what to offer: join as the signed-in user, sign in first, or create the account. */
function InvitationActions({ invitation, token, onGone }: ActionsProps) {
  const router = useRouter()
  const queryClient = useQueryClient()
  const me = useMe()
  const accept = useAcceptInvitation()
  const [failure, setFailure] = useState<AuthError | null>(null)
  const [joined, setJoined] = useState(false)
  // A new-account form answered "an account exists for this address" (409).
  const [mustSignIn, setMustSignIn] = useState(false)

  function finish(user: Me) {
    setJoined(true)
    startSession(queryClient, user)
    toast.success(`You've joined ${user.active_agency?.name ?? invitation.agency.name}`)
    router.replace("/")
  }

  function refused(cause: unknown): boolean {
    if (isApiProblem(cause) && cause.status === 410) {
      onGone(cause.detail ?? GONE_FALLBACK)
      return true
    }
    return false
  }

  /** Join with the session this browser has (signed in as the invited address). */
  function join() {
    setFailure(null)
    accept.mutate(
      { token },
      { onSuccess: finish, onError: (cause) => refused(cause) || setFailure(authError(cause)) },
    )
  }

  if (me.isPending) {
    return (
      <div aria-busy="true" className="flex flex-col gap-3">
        <span className="sr-only">Checking whether you are signed in</span>
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-full" />
      </div>
    )
  }
  if (me.isError && !isUnauthorized(me.error)) {
    const { title, description } = authError(me.error)
    return (
      <div className="flex flex-col gap-4">
        <FormAlert title={title}>{description}</FormAlert>
        <Button variant="outline" onClick={() => void me.refetch()} disabled={me.isFetching}>
          <RotateCwIcon data-icon="inline-start" />
          Try again
        </Button>
      </div>
    )
  }

  const signedIn = me.data
  if (signedIn && sameAddress(signedIn.user.email, invitation.email)) {
    const busy = accept.isPending || joined
    return (
      <div className="flex flex-col gap-5">
        <p className="text-ink-2">
          You&apos;re signed in as <span className="font-medium text-foreground">{signedIn.user.email}</span>.
        </p>
        {failure && <FormAlert title={failure.title}>{failure.description}</FormAlert>}
        <Button size="lg" className="w-full" onClick={join} disabled={busy}>
          {busy && <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />}
          {busy ? "Joining…" : `Join ${invitation.agency.name}`}
        </Button>
      </div>
    )
  }
  if (signedIn) return <WrongAccount signedInAs={signedIn.user.email} invitedEmail={invitation.email} />

  if (invitation.account_exists || mustSignIn) {
    return (
      <SignInToAccept
        invitation={invitation}
        token={token}
        joining={accept.isPending || joined}
        joinFailure={failure}
        onGone={onGone}
        onSignedIn={(user) => {
          // Signed in as the invited address: show it, and join right away.
          rememberCsrfToken(user.csrf_token)
          queryClient.setQueryData(queryKeys.me.all, user)
          join()
        }}
      />
    )
  }
  return (
    <NewAccount
      invitation={invitation}
      token={token}
      onJoined={finish}
      onGone={onGone}
      onAccountExists={() => setMustSignIn(true)}
    />
  )
}

/** Signed in, but as someone else: the invitation is for another address. */
function WrongAccount({ signedInAs, invitedEmail }: { signedInAs: string; invitedEmail: string }) {
  const signOut = useSignOutHere()
  return (
    <div className="flex flex-col gap-5">
      <FormAlert tone="info">
        You&apos;re signed in as <span className="font-medium">{signedInAs}</span>, but this invitation is for{" "}
        <span className="font-medium">{invitedEmail}</span>.
      </FormAlert>
      {signOut.isError && (
        <FormAlert title={authError(signOut.error).title}>{authError(signOut.error).description}</FormAlert>
      )}
      <div className="flex flex-col gap-2">
        <Button size="lg" className="w-full" onClick={() => signOut.mutate()} disabled={signOut.isPending}>
          {signOut.isPending && <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />}
          Sign out and continue
        </Button>
        <Button variant="ghost" asChild className="w-full">
          <Link href="/">Stay signed in as {signedInAs}</Link>
        </Button>
      </div>
    </div>
  )
}

const signInSchema = z.object({ password: z.string().min(1, "Enter your password") })
type SignInValues = z.infer<typeof signInSchema>

/** An account exists for the invited address: sign in with it, then join. */
function SignInToAccept({
  invitation,
  token,
  joining,
  joinFailure,
  onGone,
  onSignedIn,
}: {
  invitation: InvitationPreview
  token: string
  joining: boolean
  joinFailure: AuthError | null
  onGone: (detail: string) => void
  onSignedIn: (user: Me) => void
}) {
  const login = useLogin()
  const [failure, setFailure] = useState<AuthError | null>(null)
  const form = useForm<SignInValues>({ resolver: zodResolver(signInSchema), defaultValues: { password: "" } })

  function onSubmit({ password }: SignInValues) {
    setFailure(null)
    login.mutate(
      { email: invitation.email, password },
      {
        onSuccess: onSignedIn,
        onError: (cause) => {
          if (showFieldErrors(cause, form.setError, { password: "password" })) return
          if (isApiProblem(cause) && cause.status === 401) form.resetField("password")
          setFailure(authError(cause))
        },
      },
    )
  }

  const busy = login.isPending || joining
  const shown = failure ?? joinFailure

  return (
    <div className="flex flex-col gap-6">
      <p className="text-ink-2">
        You already have a HireFlow AI account. Sign in to join {invitation.agency.name} with it.
      </p>
      <SignInProviders next="/" invitationToken={token} onInvitationGone={onGone} />
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} noValidate aria-label="Sign in to join" className="flex flex-col gap-5">
          <InvitedEmail email={invitation.email} />
          <FormField
            control={form.control}
            name="password"
            render={({ field }) => (
              <FormItem>
                <div className="flex items-baseline justify-between gap-4">
                  <FormLabel>Password</FormLabel>
                  <Link href="/forgot-password" className="text-dense text-link">
                    Forgot password?
                  </Link>
                </div>
                <FormControl>
                  <PasswordInput autoComplete="current-password" disabled={busy} {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          {shown && <FormAlert title={shown.title}>{shown.description}</FormAlert>}
          <Button type="submit" size="lg" disabled={busy} className="w-full">
            {busy && <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />}
            {busy ? "Joining…" : `Sign in and join ${invitation.agency.name}`}
          </Button>
        </form>
      </Form>
    </div>
  )
}

/** The invited address, read-only: the account is created (or signed in) for it, and nothing else. */
function InvitedEmail({ email }: { email: string }) {
  return (
    <div className="grid gap-2">
      <Label htmlFor="invited-email">Email</Label>
      <Input id="invited-email" type="email" value={email} readOnly autoComplete="username" className="bg-muted" />
    </div>
  )
}

const newAccountSchema = z.object({
  displayName: z.string().trim().min(1, "Enter your name").max(200, "Use at most 200 characters"),
  password: newPasswordSchema,
})
type NewAccountValues = z.infer<typeof newAccountSchema>

/** No account for the invited address yet: choose a name and a password. */
function NewAccount({
  invitation,
  token,
  onJoined,
  onGone,
  onAccountExists,
}: {
  invitation: InvitationPreview
  token: string
  onJoined: (user: Me) => void
  onGone: (detail: string) => void
  onAccountExists: () => void
}) {
  const accept = useAcceptInvitation()
  const [failure, setFailure] = useState<AuthError | null>(null)
  const [joined, setJoined] = useState(false)
  const form = useForm<NewAccountValues>({
    resolver: zodResolver(newAccountSchema),
    defaultValues: { displayName: "", password: "" },
  })

  function onSubmit({ displayName, password }: NewAccountValues) {
    setFailure(null)
    accept.mutate(
      { token, display_name: displayName, password },
      {
        onSuccess: (user) => {
          setJoined(true)
          onJoined(user)
        },
        onError: (cause) => {
          if (isApiProblem(cause) && cause.status === 410) return onGone(cause.detail ?? GONE_FALLBACK)
          if (isApiProblem(cause) && cause.status === 409) return onAccountExists()
          if (showFieldErrors(cause, form.setError, { display_name: "displayName", password: "password" })) return
          setFailure(authError(cause))
        },
      },
    )
  }

  const busy = accept.isPending || joined

  return (
    <div className="flex flex-col gap-6">
      <SignInProviders next="/" invitationToken={token} onInvitationGone={onGone} />
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} noValidate aria-label="Create your account" className="flex flex-col gap-5">
          <InvitedEmail email={invitation.email} />
          <FormField
            control={form.control}
            name="displayName"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Your name</FormLabel>
                <FormControl>
                  <Input autoComplete="name" disabled={busy} {...field} />
                </FormControl>
                <FormDescription>How your team sees you in HireFlow AI.</FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="password"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Password</FormLabel>
                <FormControl>
                  <PasswordInput autoComplete="new-password" disabled={busy} {...field} />
                </FormControl>
                <FormDescription>{PASSWORD_HINT}</FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          {failure && <FormAlert title={failure.title}>{failure.description}</FormAlert>}
          <Button type="submit" size="lg" disabled={busy} className="w-full">
            {busy && <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />}
            {busy ? "Joining…" : `Join ${invitation.agency.name}`}
          </Button>
        </form>
      </Form>
    </div>
  )
}
