"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useQueryClient } from "@tanstack/react-query"
import { Loader2Icon } from "lucide-react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { AuthHeader } from "@/components/auth/auth-layout"
import { FormAlert } from "@/components/auth/form-alert"
import { PasswordInput } from "@/components/auth/password-input"
import { SignInProviders } from "@/components/auth/sign-in-providers"
import { Button } from "@/components/ui/button"
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { isApiProblem } from "@/lib/api/client"
import { fetchCsrfToken } from "@/lib/api/csrf"
import { selectAgencyHref } from "@/lib/auth/paths"
import { startSession, useMe, type Me } from "@/lib/auth/session"

import { authError, isRefused, showFieldErrors, type AuthError } from "./errors"
import { useLogin } from "./queries"

const loginSchema = z.object({
  email: z.string().trim().min(1, "Enter your email address").max(320, "Use at most 320 characters"),
  password: z.string().min(1, "Enter your password"),
})

type LoginValues = z.infer<typeof loginSchema>

/** Where a signed-in user goes: `next`, unless they must choose an agency first. */
export function afterSignIn(me: Me, next: string): string {
  return me.active_agency ? next : selectAgencyHref(next)
}

/** Why the page opened (`reason`, `error` in the URL), above the form. */
function LoginNotice({ reason, error }: { reason?: string; error?: string }) {
  if (error === "no_account") {
    return (
      <FormAlert tone="error" title="There is no HireFlow AI account for this address">
        Ask your agency to invite you.
      </FormAlert>
    )
  }
  if (reason === "expired") {
    return <FormAlert tone="info">Your session ended. Sign in again to carry on where you left off.</FormAlert>
  }
  if (reason === "password_changed") {
    return <FormAlert tone="success">Your password has been changed. Sign in with the new one.</FormAlert>
  }
  return null
}

/**
 * `/login` (EPIC-02 §4.3, §9.1): email and password. Errors are shown in the
 * form: one message for a wrong email or password (the API's, no account
 * enumeration), the wait after too many attempts (429, `Retry-After`), and a
 * 422 under its field. Someone already signed in goes straight on to `next`.
 */
export function Login({ next, reason, error }: { next: string; reason?: string; error?: string }) {
  const router = useRouter()
  const queryClient = useQueryClient()
  const me = useMe()
  const login = useLogin()
  const [failure, setFailure] = useState<AuthError | null>(null)
  const [leaving, setLeaving] = useState(false)
  const form = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  })

  // Already signed in (a bookmark, the back button): no form needed.
  const signedIn = me.data
  useEffect(() => {
    if (signedIn) router.replace(afterSignIn(signedIn, next))
  }, [signedIn, next, router])

  // A CSRF token for this browser before the first submit (spec §4.3, step 1).
  useEffect(() => {
    fetchCsrfToken().catch(() => undefined)
  }, [])

  function onSubmit(values: LoginValues) {
    setFailure(null)
    login.mutate(values, {
      onSuccess: (user) => {
        setLeaving(true)
        startSession(queryClient, user)
        router.replace(afterSignIn(user, next))
      },
      onError: (cause) => {
        if (showFieldErrors(cause, form.setError, { email: "email", password: "password" })) return
        if (isApiProblem(cause) && cause.status === 401) {
          form.resetField("password")
          form.setFocus("password")
        }
        const message = authError(cause)
        setFailure(
          isRefused(cause)
            ? { ...message, description: "Ask an owner or admin of your agency to give you access again." }
            : message,
        )
      },
    })
  }

  const busy = login.isPending || leaving

  return (
    <div className="flex flex-col gap-6">
      <AuthHeader title="Sign in to HireFlow AI" />
      <LoginNotice reason={reason} error={error} />
      <SignInProviders next={next} />
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} noValidate aria-label="Sign in" className="flex flex-col gap-5">
          <FormField
            control={form.control}
            name="email"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Email</FormLabel>
                <FormControl>
                  <Input type="email" inputMode="email" autoComplete="username" spellCheck={false} disabled={busy} {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
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
          {failure && <FormAlert title={failure.title}>{failure.description}</FormAlert>}
          <Button type="submit" size="lg" disabled={busy} className="w-full">
            {busy && <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />}
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </Form>
      <p className="text-dense text-muted-foreground">
        No account yet? Ask an owner or admin of your agency to invite you.
      </p>
    </div>
  )
}
