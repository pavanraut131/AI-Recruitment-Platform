"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useQueryClient } from "@tanstack/react-query"
import { Loader2Icon } from "lucide-react"
import Link from "next/link"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { AuthHeader } from "@/components/auth/auth-layout"
import { FormAlert } from "@/components/auth/form-alert"
import { PasswordInput } from "@/components/auth/password-input"
import { Button } from "@/components/ui/button"
import { Form, FormControl, FormDescription, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form"
import { isApiProblem } from "@/lib/api/client"
import { PASSWORD_HINT, newPasswordSchema } from "@/lib/auth/password"
import { loginHref } from "@/lib/auth/paths"
import { endSession } from "@/lib/auth/session"

import { authError, showFieldErrors, type AuthError } from "./errors"
import { LinkLoading, LinkProblem } from "./link-states"
import { useConfirmPasswordReset } from "./queries"
import { useFragmentToken } from "./use-fragment-token"

const resetSchema = z
  .object({ password: newPasswordSchema, confirm: z.string() })
  .refine((values) => values.confirm === values.password, {
    message: "The two passwords are different",
    path: ["confirm"],
  })

type ResetValues = z.infer<typeof resetSchema>

function AskForNewLink() {
  return (
    <Button asChild className="w-full">
      <Link href="/forgot-password">Ask for a new link</Link>
    </Button>
  )
}

/**
 * `/reset-password#token=…` (EPIC-02 §4.3, §9.1): choose a new password with
 * the token from the reset email. It follows the password policy (12 to 128
 * characters); success ends every session of the account, so the page goes
 * on to the login page with a confirmation. A used or expired link (410)
 * says so and offers a new one.
 */
export function ResetPassword() {
  const token = useFragmentToken()
  const queryClient = useQueryClient()
  const confirm = useConfirmPasswordReset()
  const [gone, setGone] = useState<string | null>(null)
  const [failure, setFailure] = useState<AuthError | null>(null)
  const [done, setDone] = useState(false)
  const form = useForm<ResetValues>({ resolver: zodResolver(resetSchema), defaultValues: { password: "", confirm: "" } })

  if (token === undefined) return <LinkLoading label="Reading the link" />
  if (token === null) {
    return (
      <LinkProblem title="This link is incomplete" action={<AskForNewLink />}>
        <p>Open the link from the email again. If it still doesn&apos;t work, ask for a new one.</p>
      </LinkProblem>
    )
  }
  if (gone !== null) {
    return (
      <LinkProblem title="This link can't be used any more" action={<AskForNewLink />}>
        <p>{gone}</p>
      </LinkProblem>
    )
  }

  function onSubmit({ password }: ResetValues) {
    setFailure(null)
    confirm.mutate(
      { token: token!, new_password: password },
      {
        onSuccess: () => {
          setDone(true)
          // Every session of the account has ended, this browser's included.
          endSession(queryClient, loginHref({ reason: "password_changed" }))
        },
        onError: (cause) => {
          if (isApiProblem(cause) && cause.status === 410) {
            setGone(cause.detail ?? "This link has expired or was already used. Ask for a new one.")
            return
          }
          if (!showFieldErrors(cause, form.setError, { new_password: "password" })) setFailure(authError(cause))
        },
      },
    )
  }

  const busy = confirm.isPending || done

  return (
    <div className="flex flex-col gap-6">
      <AuthHeader title="Choose a new password">
        <p>It replaces your old one, and signs you out on every device.</p>
      </AuthHeader>
      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          noValidate
          aria-label="Choose a new password"
          className="flex flex-col gap-5"
        >
          <FormField
            control={form.control}
            name="password"
            render={({ field }) => (
              <FormItem>
                <FormLabel>New password</FormLabel>
                <FormControl>
                  <PasswordInput autoComplete="new-password" disabled={busy} {...field} />
                </FormControl>
                <FormDescription>{PASSWORD_HINT}</FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="confirm"
            render={({ field }) => (
              <FormItem>
                <FormLabel>New password, again</FormLabel>
                <FormControl>
                  <PasswordInput autoComplete="new-password" disabled={busy} {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          {failure && <FormAlert title={failure.title}>{failure.description}</FormAlert>}
          <Button type="submit" size="lg" disabled={busy} className="w-full">
            {busy && <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />}
            {busy ? "Changing your password…" : "Change password"}
          </Button>
        </form>
      </Form>
    </div>
  )
}
