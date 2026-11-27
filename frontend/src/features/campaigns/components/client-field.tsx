"use client"

import { PlusIcon } from "lucide-react"
import { useFormContext } from "react-hook-form"

import { ClientSelect } from "@/components/clients/client-select"
import { NewClientDialog } from "@/components/clients/new-client-dialog"
import { Button } from "@/components/ui/button"
import { FormControl, FormDescription, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form"
import { usePermissions } from "@/lib/auth/permissions"
import { pickableClients, usePickerClients } from "@/lib/query/clients"

import type { CampaignFormInput, CampaignFormValues } from "../schema"

/**
 * The client a campaign is run for (EPIC-02 §9.3): required, one of the
 * active clients the user may see. Admins and owners (`client:manage`) can
 * add one inline ("New client"), which is then selected. `currentClientId`
 * (the Brief tab) keeps the campaign's own client offered even when it is
 * archived.
 */
export function CampaignClientField({
  disabled,
  currentClientId,
  description,
}: {
  disabled?: boolean
  currentClientId?: string
  description: string
}) {
  const form = useFormContext<CampaignFormInput, unknown, CampaignFormValues>()
  const clients = usePickerClients()
  const { can } = usePermissions()
  const canAdd = can("client:manage")
  const choices = pickableClients(clients.data?.items, currentClientId)
  const none = clients.data !== undefined && choices.length === 0

  let placeholder = "Choose a client"
  if (clients.isPending) placeholder = "Loading clients…"
  else if (none) placeholder = "No clients yet"

  return (
    <FormField
      control={form.control}
      name="client_id"
      render={({ field }) => (
        <FormItem>
          <FormLabel>Client</FormLabel>
          <div className="flex flex-wrap items-center gap-2">
            <FormControl>
              <ClientSelect
                clients={choices}
                value={field.value}
                onValueChange={field.onChange}
                onBlur={field.onBlur}
                ref={field.ref}
                placeholder={placeholder}
                disabled={disabled || clients.isPending || none}
              />
            </FormControl>
            {canAdd && (
              <NewClientDialog
                trigger={
                  <Button type="button" variant="ghost" className="text-ink-2" disabled={disabled}>
                    <PlusIcon data-icon="inline-start" />
                    New client
                  </Button>
                }
                onCreated={(client) =>
                  form.setValue("client_id", client.id, { shouldDirty: true, shouldValidate: true })
                }
              />
            )}
          </div>
          <FormDescription>
            {none && !canAdd
              ? "There is no client to run a campaign for yet. Ask an owner or admin of your agency to add one."
              : none
                ? "Add the company you recruit for first: New client."
                : description}
          </FormDescription>
          {clients.isError && (
            <p className="text-dense text-destructive">
              The clients could not be loaded.{" "}
              <button type="button" className="text-link" onClick={() => void clients.refetch()}>
                Try again
              </button>
            </p>
          )}
          <FormMessage />
        </FormItem>
      )}
    />
  )
}
