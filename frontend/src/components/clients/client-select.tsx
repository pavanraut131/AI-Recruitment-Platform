"use client"

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { allClientsLabel, useLimitedToSomeClients } from "@/lib/client-filter"
import { pickableClients, usePickerClients, type Client } from "@/lib/query/clients"
import { cn } from "@/lib/utils"

/** The value of "All clients" (Radix Select needs a non-empty value). */
const ALL_CLIENTS = "all"

/**
 * "All clients" or one client, for a list's toolbar (the campaign list,
 * EPIC-02 §9.3). It offers the active clients the user may see, and keeps
 * the current choice even when it is archived.
 */
export function ClientFilterSelect({
  value,
  onValueChange,
  className,
}: {
  value: string | null
  onValueChange: (clientId: string | null) => void
  className?: string
}) {
  const clients = usePickerClients()
  const limited = useLimitedToSomeClients()
  const choices = pickableClients(clients.data?.items, value)
  const unknown = value !== null && !choices.some((client) => client.id === value)

  return (
    <Select value={value ?? ALL_CLIENTS} onValueChange={(next) => onValueChange(next === ALL_CLIENTS ? null : next)}>
      <SelectTrigger aria-label="Filter by client" size="sm" className={cn("w-48 max-w-full", className)}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent position="popper" align="start">
        <SelectItem value={ALL_CLIENTS}>{allClientsLabel(limited)}</SelectItem>
        {choices.map((client) => (
          <SelectItem key={client.id} value={client.id}>
            {clientOptionLabel(client)}
          </SelectItem>
        ))}
        {unknown && (
          <SelectItem value={value}>{clients.isPending ? "Loading…" : "A client you can't see"}</SelectItem>
        )}
      </SelectContent>
    </Select>
  )
}

/** A client in a picker: its name, and "(archived)" for an archived one kept as the current choice. */
export function clientOptionLabel(client: Client): string {
  return client.status === "archived" ? `${client.name} (archived)` : client.name
}

/**
 * The client a campaign is run for, in a form (the new-campaign form and the
 * Brief tab, §9.3). The caller renders it inside `FormControl`, which gives
 * the trigger its id and error state. `clients` are the ones to offer
 * (`pickableClients`).
 */
export function ClientSelect({
  clients,
  value,
  onValueChange,
  disabled,
  placeholder = "Choose a client",
  className,
  ...trigger
}: {
  clients: readonly Client[]
  value: string
  onValueChange: (clientId: string) => void
  disabled?: boolean
  placeholder?: string
  className?: string
} & Omit<React.ComponentProps<typeof SelectTrigger>, "children" | "onChange">) {
  return (
    <Select value={value || undefined} onValueChange={onValueChange} disabled={disabled}>
      <SelectTrigger className={cn("w-72 max-w-full", className)} {...trigger}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent position="popper" align="start">
        {clients.map((client) => (
          <SelectItem key={client.id} value={client.id}>
            {clientOptionLabel(client)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
