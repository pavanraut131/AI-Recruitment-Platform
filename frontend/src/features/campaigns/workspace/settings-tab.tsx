"use client"

import * as React from "react"
import { zodResolver } from "@hookform/resolvers/zod"
import { useRouter } from "next/navigation"
import { Loader2Icon, Trash2Icon } from "lucide-react"
import { useForm } from "react-hook-form"
import { toast } from "sonner"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Form, FormControl, FormDescription, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"

import { useDeleteCampaign, useUpdateCampaign, type Campaign } from "../api"
import { CampaignFields, FormServerError } from "../components/campaign-fields"
import { applyServerErrors } from "../components/server-errors"
import {
  CAMPAIGN_STATUS_DESCRIPTIONS,
  CAMPAIGN_STATUS_LABELS,
  CAMPAIGN_STATUSES,
  campaignFormSchema,
  campaignToFormValues,
  formValuesToRequest,
  type CampaignFormInput,
  type CampaignFormValues,
} from "../schema"

const FIELDS = ["title", "role", "description", "required_skills", "status"] as const

/** The workspace's Settings tab: edit the campaign, change its status, delete it. */
export function SettingsTab({ campaign }: { campaign: Campaign }) {
  return (
    <div className="flex max-w-3xl flex-col gap-8">
      <CampaignSettingsForm campaign={campaign} />
      <DeleteCampaignSection campaign={campaign} />
    </div>
  )
}

function CampaignSettingsForm({ campaign }: { campaign: Campaign }) {
  const update = useUpdateCampaign(campaign.id)
  // Seeded once from the campaign; a save resets it to the saved values.
  // Live refetches (SSE counts) do not touch what the user is editing.
  const form = useForm<CampaignFormInput, unknown, CampaignFormValues>({
    resolver: zodResolver(campaignFormSchema),
    defaultValues: campaignToFormValues(campaign),
  })

  function onSubmit(values: CampaignFormValues) {
    update.mutate(formValuesToRequest(values), {
      onSuccess: (saved) => {
        form.reset(campaignToFormValues(saved))
        toast.success("Campaign saved")
      },
      onError: (error) => applyServerErrors(error, form.setError, FIELDS),
    })
  }

  const dirty = form.formState.isDirty

  return (
    <section aria-labelledby="campaign-settings-heading" className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 id="campaign-settings-heading" className="text-base font-semibold">
          Campaign details
        </h2>
        <p className="text-sm text-muted-foreground">
          Changing the role or skills applies to resumes scored from now on.
        </p>
      </div>
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} noValidate aria-label="Campaign settings" className="flex flex-col gap-5">
          <CampaignFields disabled={update.isPending} />
          <FormField
            control={form.control}
            name="status"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Status</FormLabel>
                <Select value={field.value} onValueChange={field.onChange} disabled={update.isPending}>
                  <FormControl>
                    <SelectTrigger className="w-48" onBlur={field.onBlur} ref={field.ref}>
                      <SelectValue />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {CAMPAIGN_STATUSES.map((status) => (
                      <SelectItem key={status} value={status}>
                        {CAMPAIGN_STATUS_LABELS[status]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <FormDescription>{CAMPAIGN_STATUS_DESCRIPTIONS[field.value ?? "active"]}</FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormServerError message={form.formState.errors.root?.server?.message} />
          <div className="flex items-center justify-end gap-2 border-t pt-4">
            <Button
              type="button"
              variant="ghost"
              disabled={!dirty || update.isPending}
              onClick={() => form.reset(campaignToFormValues(campaign))}
            >
              Discard changes
            </Button>
            <Button type="submit" disabled={!dirty || update.isPending}>
              {update.isPending && <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />}
              {update.isPending ? "Saving…" : "Save changes"}
            </Button>
          </div>
        </form>
      </Form>
    </section>
  )
}

function DeleteCampaignSection({ campaign }: { campaign: Campaign }) {
  const router = useRouter()
  const remove = useDeleteCampaign(campaign.id)
  const [open, setOpen] = React.useState(false)
  const { applications, resumes } = campaign.counts

  function confirm(event: React.MouseEvent) {
    // Keep the dialog open until the request settles; on failure the toast
    // explains and the user can retry or cancel.
    event.preventDefault()
    remove.mutate(undefined, {
      onSuccess: () => {
        toast.success("Campaign deleted", { description: campaign.title })
        setOpen(false)
        router.push("/campaigns")
      },
    })
  }

  return (
    <section
      aria-labelledby="delete-campaign-heading"
      className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-destructive/30 p-4"
    >
      <div className="flex max-w-lg flex-col gap-1">
        <h2 id="delete-campaign-heading" className="text-base font-semibold">
          Delete campaign
        </h2>
        <p className="text-sm text-muted-foreground">
          Removes the campaign with its applications, resumes and stored PDFs. People stay in Talent. This cannot be
          undone.
        </p>
      </div>
      <AlertDialog open={open} onOpenChange={(next) => !remove.isPending && setOpen(next)}>
        <AlertDialogTrigger asChild>
          <Button variant="destructive">
            <Trash2Icon data-icon="inline-start" />
            Delete campaign
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{campaign.title}”?</AlertDialogTitle>
            <AlertDialogDescription>
              This permanently deletes {plural(applications, "application")} and {plural(resumes, "resume")} with
              their PDFs. People stay in Talent with their other applications.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={remove.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={confirm} disabled={remove.isPending}>
              {remove.isPending && <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />}
              {remove.isPending ? "Deleting…" : "Delete campaign"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  )
}

function plural(count: number, noun: string): string {
  return `${count} ${count === 1 ? noun : `${noun}s`}`
}
