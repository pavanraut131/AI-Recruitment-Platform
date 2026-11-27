"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"

import { usePermissions, type Permission } from "@/lib/auth/permissions"
import { cn } from "@/lib/utils"

type Section = { href: string; title: string; permission?: Permission }

/**
 * Settings' sections (EPIC-02 §9.1), each its own page. A tab shows only for
 * the roles that can use it (§9.2: hidden, never just disabled); its page
 * shows the no-access state to anyone who opens it by URL anyway.
 */
export const SETTINGS_SECTIONS: readonly Section[] = [
  { href: "/settings/profile", title: "Profile" },
  { href: "/settings/team", title: "Team", permission: "team:read" },
  { href: "/settings/clients", title: "Clients", permission: "client:manage" },
  { href: "/settings/agency", title: "Agency", permission: "agency:manage" },
]

/** `/settings/*`: the title, the section tabs (links), then the section. */
export function SettingsLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const { can } = usePermissions()
  const sections = SETTINGS_SECTIONS.filter((section) => !section.permission || can(section.permission))

  return (
    <div className="mx-auto flex w-full max-w-[1080px] flex-col gap-6 px-4 pt-6 pb-14 sm:px-8 lg:px-14">
      <header className="flex flex-col gap-1.5">
        <h1 className="text-title font-semibold tracking-tight">Settings</h1>
        <p className="text-ink-2">
          {sections.length > 1
            ? "Your profile, and how your agency works in HireFlow AI."
            : "Your name and password."}
        </p>
      </header>
      <nav aria-label="Settings sections" className="-mb-1 overflow-x-auto border-b">
        <ul className="flex gap-6">
          {sections.map((section) => {
            const current = pathname === section.href || pathname.startsWith(`${section.href}/`)
            return (
              <li key={section.href}>
                <Link
                  href={section.href}
                  aria-current={current ? "page" : undefined}
                  className={cn(
                    "-mb-px inline-flex border-b-2 border-transparent py-2.5 text-sm whitespace-nowrap outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring",
                    current ? "border-foreground font-semibold text-foreground" : "text-muted-foreground",
                  )}
                >
                  {section.title}
                </Link>
              </li>
            )
          })}
        </ul>
      </nav>
      <div className="flex flex-col gap-10">{children}</div>
    </div>
  )
}
