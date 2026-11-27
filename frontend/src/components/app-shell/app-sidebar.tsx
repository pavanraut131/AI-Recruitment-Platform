"use client"

import { useQuery } from "@tanstack/react-query"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { SearchIcon } from "lucide-react"
import { Suspense } from "react"

import { Kbd } from "@/components/ui/kbd"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar"
import type { Schema } from "@/lib/api/client"
import { useSignedInUser } from "@/lib/auth/session"
import { clientIdParam, isClientFilteredPath, useClientFilter } from "@/lib/client-filter"
import { statsQueryOptions } from "@/lib/query/stats"
import { useCommandPalette } from "./command-palette"
import { LogoMark } from "./logo"
import { isActive, mainNav, settingsNav, type NavItem } from "./nav"
import { ThemeToggle } from "./theme-toggle"
import { useIsMac } from "./use-platform"
import { UserMenu } from "./user-menu"
import { agencyInitial, WorkspaceSwitcher } from "./workspace-switcher"

/** Nav items as the mock-ups draw them: ink-2 at rest, ink and semibold on a sunken tile when current. */
const NAV_ITEM = "h-8 gap-2.5 px-2.5 text-ink-2 data-active:font-semibold data-active:text-foreground"

/** The count beside a nav item, from the workspace stats (absent until they load, or on error). */
function navCount(item: NavItem, stats: Schema<"Stats"> | undefined): number | undefined {
  if (!stats) return undefined
  if (item.href === "/campaigns") return stats.campaigns.active
  if (item.href === "/talent") return stats.people
  return undefined
}

/** What each count is, for screen readers ("13 active", "812 people"). */
const COUNT_LABEL: Record<string, string> = { "/campaigns": "active", "/talent": "people" }

export function AppSidebar() {
  const pathname = usePathname()
  const { setOpen } = useCommandPalette()
  const isMac = useIsMac()
  const shortcut = isMac ? "⌘K" : "Ctrl K"

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="pt-4">
        {/*
          The agency and client switcher (EPIC-02 §9.2), where the D1 mock-up
          draws the agency. It reads the URL (the client), so it renders in a
          Suspense boundary; the fallback names the agency alone.
        */}
        <SidebarMenu>
          <SidebarMenuItem>
            <Suspense fallback={<AgencyName />}>
              <WorkspaceSwitcher />
            </Suspense>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent className="gap-0">
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton
                  tooltip={`Search (${shortcut})`}
                  onClick={() => setOpen(true)}
                  aria-keyshortcuts="Meta+K Control+K"
                  className={NAV_ITEM}
                >
                  <SearchIcon />
                  <span>Search</span>
                  <Kbd className="ml-auto group-data-[collapsible=icon]:hidden">{shortcut}</Kbd>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <SidebarGroup className="pt-0">
          <SidebarGroupContent>
            <Suspense fallback={<MainNav />}>
              <MainNavWithCounts />
            </Suspense>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter className="pb-4">
        <SidebarMenu className="gap-0.5">
          <SidebarMenuItem>
            <SidebarMenuButton
              asChild
              isActive={isActive(pathname, settingsNav.href)}
              tooltip={settingsNav.title}
              className={NAV_ITEM}
            >
              <Link href={settingsNav.href}>
                <settingsNav.icon />
                <span>{settingsNav.title}</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <ThemeToggle className={NAV_ITEM} />
          </SidebarMenuItem>
        </SidebarMenu>
        {/* The signed-in user under a hairline, as in the D1 mock-up. */}
        <SidebarMenu className="mt-2 border-t pt-2">
          <SidebarMenuItem>
            <UserMenu />
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}

/** The agency's name without the menu, while the switcher reads its URL state. */
function AgencyName() {
  const me = useSignedInUser()
  const name = me?.active_agency?.name
  return (
    <div className="flex h-12 items-center gap-2.5 px-2 group-data-[collapsible=icon]:px-0">
      {name ? (
        <>
          <span
            aria-hidden="true"
            className="flex size-7 shrink-0 items-center justify-center rounded-[7px] bg-foreground text-[13px] font-bold text-background"
          >
            {agencyInitial(name)}
          </span>
          <span className="truncate font-semibold group-data-[collapsible=icon]:hidden">{name}</span>
        </>
      ) : (
        <LogoMark className="size-7!" />
      )}
    </div>
  )
}

/**
 * The main navigation with its counts: the active campaigns of the chosen
 * client (what the campaign list shows for it) and the agency's people.
 */
function MainNavWithCounts() {
  const pathname = usePathname()
  const { clientId } = useClientFilter({ fromUrl: isClientFilteredPath(pathname) })
  const stats = useQuery(statsQueryOptions(clientIdParam(clientId)))
  return <MainNav stats={stats.data} />
}

function MainNav({ stats }: { stats?: Schema<"Stats"> }) {
  const pathname = usePathname()
  return (
    <SidebarMenu aria-label="Main" className="gap-0.5">
      {mainNav.map((item) => {
        const active = isActive(pathname, item.href)
        const count = navCount(item, stats)
        return (
          <SidebarMenuItem key={item.href}>
            <SidebarMenuButton asChild isActive={active} tooltip={item.title} className={NAV_ITEM}>
              <Link href={item.href} aria-current={active ? "page" : undefined}>
                <item.icon />
                <span>{item.title}</span>
              </Link>
            </SidebarMenuButton>
            {count !== undefined && (
              <SidebarMenuBadge className="top-1.5! font-normal text-muted-foreground">
                {count}
                <span className="sr-only"> {COUNT_LABEL[item.href]}</span>
              </SidebarMenuBadge>
            )}
          </SidebarMenuItem>
        )
      })}
    </SidebarMenu>
  )
}
