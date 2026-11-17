import {
  BriefcaseIcon,
  HouseIcon,
  SettingsIcon,
  UsersIcon,
  type LucideIcon,
} from "lucide-react"

export type NavItem = {
  title: string
  href: string
  icon: LucideIcon
}

/** Primary navigation (EPIC-01 §4), shared by the sidebar and ⌘K palette. */
export const mainNav: NavItem[] = [
  { title: "Home", href: "/", icon: HouseIcon },
  { title: "Campaigns", href: "/campaigns", icon: BriefcaseIcon },
  { title: "Talent", href: "/talent", icon: UsersIcon },
]

export const settingsNav: NavItem = {
  title: "Settings",
  href: "/settings",
  icon: SettingsIcon,
}

/** `/` only matches itself; other items also match their sub-routes. */
export function isActive(pathname: string, href: string) {
  if (href === "/") return pathname === "/"
  return pathname === href || pathname.startsWith(`${href}/`)
}
