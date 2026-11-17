import { cookies } from "next/headers"

import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { AppSidebar } from "./app-sidebar"
import { CommandPaletteProvider } from "./command-palette"
import { TopBar } from "./top-bar"

/**
 * The HireFlow AI shell (EPIC-01 §4): sidebar, top bar and ⌘K palette around
 * every page. A Server Component; the interactive parts are client
 * components. The sidebar's collapsed state persists in a cookie (written by
 * SidebarProvider) so the first render already matches it.
 */
export async function AppShell({ children }: { children: React.ReactNode }) {
  const cookieStore = await cookies()
  const defaultOpen = cookieStore.get("sidebar_state")?.value !== "false"

  return (
    <CommandPaletteProvider>
      <SidebarProvider defaultOpen={defaultOpen}>
        <AppSidebar />
        <SidebarInset>
          <TopBar />
          <div id="main-content" tabIndex={-1} className="flex flex-1 flex-col outline-none">
            {children}
          </div>
        </SidebarInset>
      </SidebarProvider>
    </CommandPaletteProvider>
  )
}
