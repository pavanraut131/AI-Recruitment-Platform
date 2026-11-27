"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Loader2Icon } from "lucide-react"
import Link from "next/link"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { AuthHeader } from "@/components/auth/auth-layout"
import { FormAlert } from "@/components/auth/form-alert"
import { Button } from "@/components/ui/button"
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form"
import { Input } from "@/components/ui/input"

import { authError, showFieldErrors, type AuthError } from "./errors"
import { useRequestPasswordReset } from "./queries"

const forgotSchema = z.object({
  email: z.string().trim().min(1, "Enter your email address").max(320, "Use at most 320 characters"),
})

type ForgotValues = z.infer<typeof forgotSchema>

/**
 * `/forgot-password` (EPIC-02 §4.3, §9.1): asks for a reset link. The answer
 * is the same whether or not an account exists for the address (no account
 * enumeration), so the page always shows the same confirmation.
 */
export function ForgotPassword() {
  const request = useRequestPasswordReset()
  const [sentTo, setSentTo] = useState<string | null>(null)
  const [failure, setFailure] = useState<AuthError | null>(null)
  const form = useForm<ForgotValues>({ resolver: zodResolver(forgotSchema), defaultValues: { email: "" } })

  function onSubmit({ email }: ForgotValues) {
    setFailure(null)
    request.mutate(
      { email },
      {
        onSuccess: () => setSentTo(email),
        onError: (cause) => {
          if (!showFieldErrors(cause, form.setError, { email: "email" })) setFailure(authError(cause))
        },
      },
    )
  }

  if (sentTo !== null) {
    return (
      <div className="flex flex-col gap-6">
        <AuthHeader title="Check your email">
          <p>
            If an account exists for <span className="font-medium text-foreground">{sentTo}</span>, we&apos;ve sent it
            a link to choose a new password. The link works once, for a short time.
          </p>
        </AuthHeader>
        <p className="text-dense text-muted-foreground">
          Nothing after a few minutes? Check your spam folder, or{" "}
          <button
            type="button"
            className="text-link"
            onClick={() => {
              request.reset()
              setSentTo(null)
            }}
          >
            send another link
          </button>
          .
        </p>
        <Button variant="outline" asChild className="w-full">
          <Link href="/login">Back to sign in</Link>
        </Button>
      </div>
    )
  }

  const busy = request.isPending

  return (
    <div className="flex flex-col gap-6">
      <AuthHeader title="Reset your password">
        <p>Enter the email address you sign in with, and we&apos;ll email you a link to choose a new password.</p>
      </AuthHeader>
      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          noValidate
          aria-label="Reset your password"
          className="flex flex-col gap-5"
        >
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
          {failure && <FormAlert title={failure.title}>{failure.description}</FormAlert>}
          <Button type="submit" size="lg" disabled={busy} className="w-full">
            {busy && <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />}
            {busy ? "Sending…" : "Email me a link"}
          </Button>
        </form>
      </Form>
      <p className="text-dense">
        <Link href="/login" className="text-link">
          Back to sign in
        </Link>
      </p>
    </div>
  )
}
