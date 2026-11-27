import { SidebarTrigger } from "@/components/ui/sidebar"
import { Breadcrumbs } from "./breadcrumbs"

/**
 * The slim bar above every page: the sidebar toggle and the breadcrumbs, with
 * no border of its own (the page's header is the focal point). Pages render
 * their own actions in their header; the user menu is at the foot of the sidebar.
 */
export function TopBar() {
  return (
    <header className="sticky top-0 z-10 flex h-12 shrink-0 items-center gap-2 bg-background/95 px-3 backdrop-blur-sm sm:px-4">
      <SidebarTrigger className="text-muted-foreground hover:text-foreground" />
      <Breadcrumbs />
    </header>
  )
}
