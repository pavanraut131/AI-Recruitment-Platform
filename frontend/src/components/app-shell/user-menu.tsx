"use client"

import { ChevronsUpDownIcon, LogOutIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { SidebarMenuButton, useSidebar } from "@/components/ui/sidebar"
import { initials, ROLE_LABELS } from "@/lib/auth/roles"
import { useSignedInUser, useSignOut } from "@/lib/auth/session"

/**
 * The signed-in user, at the foot of the sidebar (EPIC-02 §9.2; the D1
 * mock-up's avatar, name and role). Its menu shows the email, the agency
 * they are working in and their role there, and signs out: `POST
 * /auth/logout`, then the cache is cleared and the login page loads.
 * Choosing another agency is the sidebar header's switcher (E2-10).
 */
export function UserMenu() {
  const me = useSignedInUser()
  const signOut = useSignOut()
  const { isMobile } = useSidebar()
  if (!me) return null

  const { user, active_agency: agency, role } = me
  const roleLabel = role ? ROLE_LABELS[role] : null
  const context = [roleLabel, agency?.name].filter(Boolean).join(" · ")

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <SidebarMenuButton size="lg" tooltip={user.display_name} className="gap-2.5 px-2 data-[state=open]:bg-sidebar-accent">
          <span
            aria-hidden="true"
            className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary-soft text-xs font-semibold text-primary"
          >
            {initials(user.display_name)}
          </span>
          <span className="flex min-w-0 flex-1 flex-col gap-0.5 leading-tight">
            <span className="truncate font-medium text-foreground">{user.display_name}</span>
            {context && <span className="truncate text-xs text-muted-foreground">{context}</span>}
          </span>
          <ChevronsUpDownIcon className="ml-auto text-muted-foreground" aria-hidden="true" />
        </SidebarMenuButton>
      </DropdownMenuTrigger>
      <DropdownMenuContent side={isMobile ? "top" : "right"} align="end" sideOffset={8} className="w-auto min-w-64">
        <DropdownMenuLabel className="flex flex-col gap-0.5 px-2 py-1.5 text-sm font-normal">
          <span className="font-medium text-foreground">{user.display_name}</span>
          <span className="text-dense text-muted-foreground">{user.email}</span>
        </DropdownMenuLabel>
        {agency && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuLabel className="flex items-center justify-between gap-3 px-2 py-1.5 text-sm font-normal">
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className="text-xs text-muted-foreground">Agency</span>
                  <span className="truncate text-foreground">{agency.name}</span>
                </span>
                {roleLabel && <Badge variant="secondary">{roleLabel}</Badge>}
              </DropdownMenuLabel>
            </DropdownMenuGroup>
          </>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => signOut.mutate()} disabled={signOut.isPending} className="px-2">
          <LogOutIcon />
          {signOut.isPending ? "Signing out…" : "Sign out"}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
