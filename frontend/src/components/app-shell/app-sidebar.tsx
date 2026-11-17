"use client"

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
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar"
import { useCommandPalette } from "./command-palette"
import { LogoMark } from "./logo"
import { isActive, mainNav, settingsNav } from "./nav"
import { ThemeToggle } from "./theme-toggle"
import { useIsMac } from "./use-platform"

export function AppSidebar() {
  const pathname = usePathname()
  const { setOpen } = useCommandPalette()
  const isMac = useIsMac()
  const shortcut = isMac ? "⌘K" : "Ctrl K"

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            {/* Workspace switcher slot: agencies and workspaces arrive in E2. */}
            {/* Static for now; becomes a switcher button in E2. */}
            <SidebarMenuButton
              asChild
              size="lg"
              className="cursor-default hover:bg-transparent active:bg-transparent"
            >
              <div>
                <LogoMark className="size-8" />
                <span className="grid flex-1 text-left leading-tight">
                  <span className="truncate font-semibold">HireFlow AI</span>
                  <span className="truncate text-xs text-muted-foreground">
                    Agency workspace
                  </span>
                </span>
              </div>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton
                  tooltip={`Search (${shortcut})`}
                  onClick={() => setOpen(true)}
                  aria-keyshortcuts="Meta+K Control+K"
                  className="text-muted-foreground"
                >
                  <SearchIcon />
                  <span>Search</span>
                  <Kbd className="ml-auto group-data-[collapsible=icon]:hidden">
                    {shortcut}
                  </Kbd>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu aria-label="Main">
              {mainNav.map((item) => {
                const active = isActive(pathname, item.href)
                return (
                  <SidebarMenuItem key={item.href}>
                    <SidebarMenuButton
                      asChild
                      isActive={active}
                      tooltip={item.title}
                      className="data-active:text-sidebar-primary"
                    >
                      <Link
                        href={item.href}
                        aria-current={active ? "page" : undefined}
                      >
                        <item.icon />
                        <span>{item.title}</span>
                      </Link>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                )
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              asChild
              isActive={isActive(pathname, settingsNav.href)}
              tooltip={settingsNav.title}
              className="data-active:text-sidebar-primary"
            >
              <Link href={settingsNav.href}>
                <settingsNav.icon />
                <span>{settingsNav.title}</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <ThemeToggle />
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
