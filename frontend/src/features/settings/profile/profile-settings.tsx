"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Loader2Icon } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { toast } from "sonner"
import { z } from "zod"

import { FormAlert } from "@/components/auth/form-alert"
import { PasswordInput } from "@/components/auth/password-input"
import { Button } from "@/components/ui/button"
import { Form, FormControl, FormDescription, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { newPasswordSchema, PASSWORD_HINT } from "@/lib/auth/password"
import { ROLE_IN_SENTENCE } from "@/lib/auth/roles"
import { useSignedInUser, type Me } from "@/lib/auth/session"
import { problemNotice, setFieldErrors, type ProblemNotice } from "@/lib/forms"

import { useChangePassword, useUpdateProfile } from "../queries"
import { SettingsSection } from "../section"

/** `MeUpdate.display_name` in the v2 document. */
const DISPLAY_NAME_MAX = 200

/**
 * Settings → Profile (EPIC-02 §9.1), for every member: the name others see,
 * the sign-in email (read-only: no email change in E2), and the password.
 */
export function ProfileSettings() {
  const me = useSignedInUser()
  if (!me) return null
  return (
    <>
      <NameSection me={me} />
      <PasswordSection email={me.user.email} />
    </>
  )
}

const nameSchema = z.object({
  display_name: z
    .string()
    .trim()
    .min(1, "Enter the name your team sees.")
    .max(DISPLAY_NAME_MAX, `Keep it under ${DISPLAY_NAME_MAX} characters.`),
})
type NameValues = z.infer<typeof nameSchema>

function NameSection({ me }: { me: Me }) {
  const update = useUpdateProfile()
  const [failure, setFailure] = useState<ProblemNotice | null>(null)
  const form = useForm<NameValues>({
    resolver: zodResolver(nameSchema),
    defaultValues: { display_name: me.user.display_name },
  })

  function submit(values: NameValues) {
    setFailure(null)
    update.mutate(values, {
      onSuccess: (saved) => {
        form.reset({ display_name: saved.user.display_name })
        toast.success("Name saved")
      },
      onError: (error) => {
        if (!setFieldErrors(error, form.setError, { display_name: "display_name" })) setFailure(problemNotice(error))
      },
    })
  }

  return (
    <SettingsSection
      id="profile-heading"
      title="Your profile"
      description={
        me.role && me.active_agency ? `You're ${ROLE_IN_SENTENCE[me.role]} in ${me.active_agency.name}.` : undefined
      }
    >
      <Form {...form}>
        <form onSubmit={form.handleSubmit(submit)} noValidate aria-label="Your profile" className="flex max-w-md flex-col gap-5">
          <FormField
            control={form.control}
            name="display_name"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Name</FormLabel>
                <FormControl>
                  <Input autoComplete="name" disabled={update.isPending} {...field} />
                </FormControl>
                <FormDescription>How your team sees you, in the team list and on your work.</FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <div className="flex flex-col gap-1.5">
            <p className="text-sm font-medium" id="profile-email-label">
              Email
            </p>
            <p aria-labelledby="profile-email-label" className="text-sm">
              {me.user.email}
            </p>
            <p className="text-dense text-muted-foreground">You sign in with this address. It can&apos;t be changed here.</p>
          </div>
          {failure && <FormAlert title={failure.title}>{failure.description}</FormAlert>}
          <div>
            <Button type="submit" disabled={!form.formState.isDirty || update.isPending}>
              {update.isPending && <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />}
              {update.isPending ? "Saving…" : "Save name"}
            </Button>
          </div>
        </form>
      </Form>
    </SettingsSection>
  )
}

const passwordSchema = z
  .object({
    current_password: z.string().min(1, "Enter your current password."),
    new_password: newPasswordSchema,
    confirm: z.string(),
  })
  .refine((values) => values.confirm === values.new_password, {
    path: ["confirm"],
    message: "The passwords don't match.",
  })
type PasswordValues = z.infer<typeof passwordSchema>

const EMPTY_PASSWORDS: PasswordValues = { current_password: "", new_password: "", confirm: "" }

function PasswordSection({ email }: { email: string }) {
  const change = useChangePassword()
  const [failure, setFailure] = useState<ProblemNotice | null>(null)
  const [changed, setChanged] = useState(false)
  const form = useForm<PasswordValues>({ resolver: zodResolver(passwordSchema), defaultValues: EMPTY_PASSWORDS })

  function submit({ current_password, new_password }: PasswordValues) {
    setFailure(null)
    setChanged(false)
    change.mutate(
      { current_password, new_password },
      {
        onSuccess: () => {
          form.reset(EMPTY_PASSWORDS)
          setChanged(true)
          toast.success("Password changed")
        },
        onError: (error) => {
          const mapped = setFieldErrors(error, form.setError, {
            current_password: "current_password",
            new_password: "new_password",
          })
          if (!mapped) setFailure(problemNotice(error))
        },
      },
    )
  }

  const busy = change.isPending
  return (
    <SettingsSection
      id="password-heading"
      title="Password"
      description="Changing it signs you out on every other browser and device. You stay signed in here."
    >
      <Form {...form}>
        <form onSubmit={form.handleSubmit(submit)} noValidate aria-label="Change password" className="flex max-w-md flex-col gap-5">
          {/* For password managers: the account these passwords belong to. */}
          <input type="text" name="username" autoComplete="username" value={email} readOnly hidden />
          <FormField
            control={form.control}
            name="current_password"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Current password</FormLabel>
                <FormControl>
                  <PasswordInput autoComplete="current-password" disabled={busy} {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="new_password"
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
          {changed && (
            <FormAlert tone="success" title="Your password has been changed">
              Every other browser and device is signed out.
            </FormAlert>
          )}
          <div>
            <Button type="submit" disabled={busy}>
              {busy && <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />}
              {busy ? "Changing…" : "Change password"}
            </Button>
          </div>
        </form>
      </Form>
    </SettingsSection>
  )
}
