"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Loader2Icon } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { toast } from "sonner"
import { z } from "zod"

import { FormAlert } from "@/components/auth/form-alert"
import { RequirePermission } from "@/components/auth/no-access"
import { ErrorState } from "@/components/data-states"
import { Button } from "@/components/ui/button"
import { Form, FormControl, FormDescription, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { problemNotice, setFieldErrors, type ProblemNotice } from "@/lib/forms"

import { useAgency, useUpdateAgency, type Agency } from "../queries"
import { SettingsSection } from "../section"

/** `AgencyUpdate.name` in the v2 document. */
const AGENCY_NAME_MAX = 200

const agencySchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Enter the agency's name.")
    .max(AGENCY_NAME_MAX, `Keep it under ${AGENCY_NAME_MAX} characters.`),
})
type AgencyValues = z.infer<typeof agencySchema>

/**
 * Settings → Agency (EPIC-02 §9.1, `agency:manage`: owners): the agency's
 * name, which everyone in it sees in the switcher and in invitation emails.
 */
export function AgencySettings() {
  return (
    <RequirePermission
      permission="agency:manage"
      what="change the agency's settings"
      who="Owners can."
      back={{ href: "/settings/profile", label: "Go to your profile" }}
    >
      <AgencySection />
    </RequirePermission>
  )
}

function AgencySection() {
  const agency = useAgency()
  return (
    <SettingsSection
      id="agency-heading"
      title="Agency"
      description="Everyone in the agency sees its name, and so does everyone you invite."
    >
      {agency.isPending ? (
        <div className="flex max-w-md flex-col gap-2" aria-busy="true" aria-label="Loading the agency">
          <Skeleton className="h-4 w-16" />
          <Skeleton className="h-9 w-full" />
        </div>
      ) : agency.isError ? (
        <ErrorState error={agency.error} onRetry={() => void agency.refetch()} retrying={agency.isFetching} />
      ) : (
        <AgencyForm agency={agency.data} />
      )}
    </SettingsSection>
  )
}

function AgencyForm({ agency }: { agency: Agency }) {
  const update = useUpdateAgency()
  const [failure, setFailure] = useState<ProblemNotice | null>(null)
  const form = useForm<AgencyValues>({ resolver: zodResolver(agencySchema), defaultValues: { name: agency.name } })

  function submit(values: AgencyValues) {
    setFailure(null)
    update.mutate(values, {
      onSuccess: (saved) => {
        form.reset({ name: saved.name })
        toast.success("Agency renamed", { description: saved.name })
      },
      onError: (error) => {
        if (!setFieldErrors(error, form.setError, { name: "name" })) setFailure(problemNotice(error))
      },
    })
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(submit)} noValidate aria-label="Agency" className="flex max-w-md flex-col gap-5">
        <FormField
          control={form.control}
          name="name"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Name</FormLabel>
              <FormControl>
                <Input autoComplete="organization" disabled={update.isPending} {...field} />
              </FormControl>
              <FormDescription>
                Its short name, <span className="font-medium text-ink-2">{agency.slug}</span>, stays the same.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        {failure && <FormAlert title={failure.title}>{failure.description}</FormAlert>}
        <div>
          <Button type="submit" disabled={!form.formState.isDirty || update.isPending}>
            {update.isPending && <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />}
            {update.isPending ? "Saving…" : "Save name"}
          </Button>
        </div>
      </form>
    </Form>
  )
}
