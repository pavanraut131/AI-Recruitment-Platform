"use client"

import { useQuery } from "@tanstack/react-query"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { SearchIcon } from "lucide-react"

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
import { statsQueryOptions } from "@/lib/query/stats"
import { useCommandPalette } from "./command-palette"
import { LogoMark } from "./logo"
import { isActive, mainNav, settingsNav, type NavItem } from "./nav"
import { ThemeToggle } from "./theme-toggle"
import { useIsMac } from "./use-platform"

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
  const stats = useQuery(statsQueryOptions())

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="pt-4">
        {/*
          Product and workspace header. E2-10 turns it into the agency
          switcher (agency name, then "All clients"); until then it names the
          product only, never an agency.
        */}
        <div className="flex h-9 items-center gap-2.5 px-1 group-data-[collapsible=icon]:px-0">
          <LogoMark className="size-7!" />
          <span className="truncate text-section font-semibold tracking-tight group-data-[collapsible=icon]:hidden">
            HireFlow AI
          </span>
        </div>
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
            <SidebarMenu aria-label="Main" className="gap-0.5">
              {mainNav.map((item) => {
                const active = isActive(pathname, item.href)
                const count = navCount(item, stats.data)
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
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
