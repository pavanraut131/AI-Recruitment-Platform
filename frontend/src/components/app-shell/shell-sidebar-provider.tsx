"use client"

import { usePathname } from "next/navigation"
import { useState, type ReactNode } from "react"

import { SidebarProvider } from "@/components/ui/sidebar"

/** The campaign workspace (`/campaigns/<id>`, not `/campaigns/new`): the table needs the width. */
export function isWorkspacePath(pathname: string): boolean {
  return /^\/campaigns\/(?!new(?:\/|$))[^/]+\/?$/.test(pathname)
}

/**
 * The sidebar's open state (docs/design/README.md, layout rules). Everywhere
 * else it is the user's choice, kept in a cookie; in a campaign workspace it
 * collapses to an icon rail so the candidates table and the review panel get
 * the width. Expanding it there lasts until the user leaves that campaign, and
 * never overwrites the cookie.
 */
export function ShellSidebarProvider({ defaultOpen, children }: { defaultOpen: boolean; children: ReactNode }) {
  const pathname = usePathname()
  const [open, setOpen] = useState(defaultOpen)
  // The workspace path the user expanded the rail on, if any.
  const [expandedOn, setExpandedOn] = useState<string | null>(null)
  const inWorkspace = isWorkspacePath(pathname)

  return (
    <SidebarProvider
      open={inWorkspace ? expandedOn === pathname : open}
      onOpenChange={(next) => (inWorkspace ? setExpandedOn(next ? pathname : null) : setOpen(next))}
      persistOpen={!inWorkspace}
    >
      {children}
    </SidebarProvider>
  )
}
