"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { ChevronsUpDownIcon, SettingsIcon } from "lucide-react"
import { useId } from "react"

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { SidebarMenuButton, useSidebar } from "@/components/ui/sidebar"
import { usePermissions } from "@/lib/auth/permissions"
import { ROLE_LABELS } from "@/lib/auth/roles"
import { useSignedInUser, useSwitchAgency } from "@/lib/auth/session"
import { allClientsLabel, isClientFilteredPath, useClientFilter } from "@/lib/client-filter"
import { clientName, pickableClients, usePickerClients } from "@/lib/query/clients"

/** The radio value of "All clients" (Radix needs a non-empty value). */
const ALL_CLIENTS = "all"

/**
 * The sidebar header (EPIC-02 §9.2; the D1 mock-up's agency button): the
 * agency's initial on an ink tile, its name, and the client Home and the
 * campaign list show. Its menu changes either:
 *
 * - **Agencies**, only for someone in more than one (§15 Q1): choosing one
 *   calls `PUT /me/active-agency`, clears the Query cache and opens Home as a
 *   new page (`useSwitchAgency`).
 * - **Clients**: "All clients" or one of the user's active clients
 *   (`GET /clients` lists only those they may see). On Home and the campaign
 *   list the choice goes in the URL; everywhere it is remembered per agency
 *   as the default (`useClientFilter`).
 */
export function WorkspaceSwitcher() {
  const me = useSignedInUser()
  const pathname = usePathname()
  const { isMobile } = useSidebar()
  const { can } = usePermissions()
  const filter = useClientFilter({ fromUrl: isClientFilteredPath(pathname) })
  const clients = usePickerClients()
  const switchAgency = useSwitchAgency()
  const agenciesLabel = useId()
  const clientsLabel = useId()

  const agency = me?.active_agency
  if (!me || !agency) return null

  const limited = me.client_scope?.all_clients === false
  const choices = pickableClients(clients.data?.items, filter.clientId)
  const chosenName = clientName(clients.data?.items, filter.clientId)
  const clientLabel = filter.clientId
    ? (chosenName ?? (clients.isPending ? "Loading…" : "A client you can't see"))
    : allClientsLabel(limited)
  const switching = switchAgency.isPending
  const switchingTo = switching ? me.memberships.find((m) => m.agency.id === switchAgency.variables)?.agency.name : null
  const secondLine = switchingTo ? `Switching to ${switchingTo}…` : clientLabel

  return (
    // Not modal, like the review panel: the page behind it stays in the accessibility tree.
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <SidebarMenuButton
          size="lg"
          tooltip={`${agency.name} · ${clientLabel}`}
          aria-busy={switching || undefined}
          className="gap-2.5 px-2 data-[state=open]:bg-sidebar-accent"
        >
          <span
            aria-hidden="true"
            className="flex size-7 shrink-0 items-center justify-center rounded-[7px] bg-foreground text-[13px] font-bold text-background"
          >
            {agencyInitial(agency.name)}
          </span>
          <span className="flex min-w-0 flex-1 flex-col gap-0.5 leading-tight">
            <span className="truncate font-semibold text-foreground">{agency.name}</span>
            <span className="truncate text-xs text-muted-foreground">{secondLine}</span>
          </span>
          <ChevronsUpDownIcon className="ml-auto text-muted-foreground" aria-hidden="true" />
          <span className="sr-only">: change the agency or client</span>
        </SidebarMenuButton>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        side={isMobile ? "bottom" : "right"}
        align="start"
        sideOffset={8}
        className="w-72 max-w-[calc(100vw-2rem)]"
      >
        {me.memberships.length > 1 && (
          <>
            <DropdownMenuGroup aria-labelledby={agenciesLabel}>
              <DropdownMenuLabel id={agenciesLabel} className="text-xs font-normal text-muted-foreground">
                Agencies
              </DropdownMenuLabel>
              <DropdownMenuRadioGroup
                value={agency.id}
                onValueChange={(agencyId) => {
                  if (agencyId !== agency.id) switchAgency.mutate(agencyId)
                }}
              >
                {me.memberships.map((membership) => (
                  <DropdownMenuRadioItem key={membership.agency.id} value={membership.agency.id} disabled={switching}>
                    <span className="flex min-w-0 flex-col">
                      <span className="truncate">{membership.agency.name}</span>
                      <span className="text-xs text-muted-foreground">{ROLE_LABELS[membership.role]}</span>
                    </span>
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
          </>
        )}
        <DropdownMenuGroup aria-labelledby={clientsLabel}>
          <DropdownMenuLabel id={clientsLabel} className="text-xs font-normal text-muted-foreground">
            Clients
          </DropdownMenuLabel>
          <div className="max-h-64 overflow-y-auto">
            <DropdownMenuRadioGroup
              value={filter.clientId ?? ALL_CLIENTS}
              onValueChange={(value) => filter.setClientId(value === ALL_CLIENTS ? null : value)}
            >
              <DropdownMenuRadioItem value={ALL_CLIENTS}>{allClientsLabel(limited)}</DropdownMenuRadioItem>
              {choices.map((client) => (
                <DropdownMenuRadioItem key={client.id} value={client.id}>
                  <span className="truncate">{client.name}</span>
                  {client.status === "archived" && <span className="text-xs text-muted-foreground">Archived</span>}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
            {clients.isPending && (
              <DropdownMenuItem disabled className="text-muted-foreground">
                Loading clients…
              </DropdownMenuItem>
            )}
            {clients.isError && (
              <DropdownMenuItem onSelect={() => void clients.refetch()}>
                Couldn&apos;t load the clients. Try again
              </DropdownMenuItem>
            )}
          </div>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <p className="px-1.5 py-1 text-xs text-muted-foreground">
          Home and Campaigns show the chosen client. Talent shows everyone in the agency.
        </p>
        {can("client:manage") && (
          <DropdownMenuItem asChild>
            <Link href="/settings/clients">
              <SettingsIcon aria-hidden="true" />
              Manage clients
            </Link>
          </DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** The first letter of the agency's name, for its tile ("Northbank Talent" → "N"). */
export function agencyInitial(name: string): string {
  const letter = [...name.trim()].find((character) => /[\p{L}\p{N}]/u.test(character))
  return letter ? letter.toUpperCase() : "?"
}
