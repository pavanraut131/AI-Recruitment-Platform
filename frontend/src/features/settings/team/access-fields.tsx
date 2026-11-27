"use client"

import { useId } from "react"
import { useFormContext, useWatch } from "react-hook-form"

import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { ROLE_DESCRIPTIONS, ROLE_LABELS, type Role } from "@/lib/auth/roles"
import { pickableClients, usePickerClients } from "@/lib/query/clients"
import { cn } from "@/lib/utils"

/*
 * The access a teammate gets (EPIC-02 §5.1, §5.3): a role, and for recruiters
 * and viewers "All clients" or some of them. Shared by the invite dialog and
 * the member's client-access dialog; both forms name these fields `role`,
 * `all_clients` and `client_ids`, as the API does.
 */

export type AccessValues = { role: Role; all_clients: boolean; client_ids: string[] }

/** The role, as radio buttons with what each may do; `roles` are the ones the user may give. */
export function RoleField({ roles, disabled }: { roles: readonly Role[]; disabled?: boolean }) {
  const form = useFormContext<AccessValues>()
  const legendId = useId()
  const value = useWatch({ control: form.control, name: "role" })
  return (
    <fieldset className="flex flex-col gap-2.5">
      <legend id={legendId} className="mb-2.5 text-sm font-medium">
        Role
      </legend>
      <RadioGroup
        aria-labelledby={legendId}
        value={value}
        onValueChange={(role) => form.setValue("role", role as Role, { shouldDirty: true })}
        disabled={disabled}
        className="gap-2.5"
      >
        {roles.map((role) => (
          <Choice key={role} value={role} label={ROLE_LABELS[role]} hint={ROLE_DESCRIPTIONS[role]} />
        ))}
      </RadioGroup>
    </fieldset>
  )
}

/**
 * "All clients" or "Only some clients" with a checkbox per active client the
 * user may see. Owners and admins always have every client, so a privileged
 * `role` shows a sentence instead.
 */
export function ClientAccessFields({ role, disabled }: { role: Role; disabled?: boolean }) {
  const form = useFormContext<AccessValues>()
  const legendId = useId()
  const listLabelId = useId()
  const errorId = useId()
  const clients = usePickerClients()
  const allClients = useWatch({ control: form.control, name: "all_clients" })
  const chosen = useWatch({ control: form.control, name: "client_ids" })
  const error = form.formState.errors.client_ids?.message

  if (role === "owner" || role === "admin") {
    return (
      <div className="flex flex-col gap-1">
        <p className="text-sm font-medium">Client access</p>
        <p className="text-dense text-muted-foreground">
          {ROLE_LABELS[role]}s always see every client&apos;s campaigns.
        </p>
      </div>
    )
  }

  const active = pickableClients(clients.data?.items)
  function toggle(clientId: string, checked: boolean) {
    const next = checked ? [...chosen, clientId] : chosen.filter((id) => id !== clientId)
    form.clearErrors("client_ids")
    form.setValue("client_ids", next, { shouldDirty: true, shouldValidate: form.formState.isSubmitted })
  }

  return (
    <fieldset className="flex flex-col gap-2.5">
      <legend id={legendId} className="mb-2.5 text-sm font-medium">
        Client access
      </legend>
      <RadioGroup
        aria-labelledby={legendId}
        value={allClients ? "all" : "some"}
        onValueChange={(value) => {
          form.clearErrors("client_ids")
          form.setValue("all_clients", value === "all", { shouldDirty: true, shouldValidate: form.formState.isSubmitted })
        }}
        disabled={disabled}
        className="gap-2.5"
      >
        <Choice value="all" label="All clients" hint="Every client's campaigns, including clients added later." />
        <Choice value="some" label="Only some clients" hint="Only the campaigns of the clients you choose." />
      </RadioGroup>
      {!allClients && (
        <div className="ml-6.5 flex flex-col gap-2">
          <p id={listLabelId} className="sr-only">
            Clients
          </p>
          {clients.isPending ? (
            <p className="text-dense text-muted-foreground">Loading clients…</p>
          ) : active.length === 0 ? (
            <p className="text-dense text-muted-foreground">There are no active clients to choose from yet.</p>
          ) : (
            <div
              role="group"
              aria-labelledby={listLabelId}
              aria-describedby={error ? errorId : undefined}
              className="flex max-h-48 flex-col gap-2 overflow-y-auto rounded-lg bg-canvas p-3"
            >
              {active.map((client) => {
                const id = `${legendId}-${client.id}`
                return (
                  <div key={client.id} className="flex items-center gap-2.5">
                    <Checkbox
                      id={id}
                      checked={chosen.includes(client.id)}
                      onCheckedChange={(checked) => toggle(client.id, checked === true)}
                      disabled={disabled}
                      aria-invalid={error ? true : undefined}
                    />
                    <Label htmlFor={id} className="font-normal">
                      {client.name}
                    </Label>
                  </div>
                )
              })}
            </div>
          )}
          {error && (
            <p id={errorId} className="text-sm text-destructive">
              {error}
            </p>
          )}
        </div>
      )}
    </fieldset>
  )
}

/** One radio button with its label and a line on what it means. */
function Choice({ value, label, hint }: { value: string; label: string; hint: string }) {
  const id = useId()
  const hintId = useId()
  return (
    <div className="flex items-start gap-2.5">
      <RadioGroupItem id={id} value={value} aria-describedby={hintId} className="mt-0.5" />
      <div className="flex flex-col gap-0.5">
        <Label htmlFor={id}>{label}</Label>
        <p id={hintId} className={cn("text-dense text-muted-foreground")}>
          {hint}
        </p>
      </div>
    </div>
  )
}

/** The request's client fields for a role: owners and admins always every client. */
export function accessRequest({ role, all_clients, client_ids }: AccessValues): {
  all_clients: boolean
  client_ids: string[]
} {
  if (role === "owner" || role === "admin" || all_clients) return { all_clients: true, client_ids: [] }
  return { all_clients: false, client_ids }
}

/** "Choose at least one client" when a recruiter or viewer is limited to none (as the API refuses it). */
export function accessIssue({ role, all_clients, client_ids }: AccessValues): string | null {
  if (role === "owner" || role === "admin" || all_clients) return null
  return client_ids.length === 0 ? "Choose at least one client." : null
}
