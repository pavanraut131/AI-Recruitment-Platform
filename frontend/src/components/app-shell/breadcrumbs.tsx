"use client"

import * as React from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"

import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"

const LABELS: Record<string, string> = {
  campaigns: "Campaigns",
  candidates: "Candidates",
  people: "People",
  talent: "Talent",
  settings: "Settings",
  new: "New campaign",
  dev: "Developer",
  ui: "UI gallery",
}

/** Segments with an index page; others render as plain text, not links. */
const LINKABLE = new Set(["/campaigns", "/people", "/talent", "/settings"])

function label(segment: string) {
  const decoded = decodeURIComponent(segment)
  if (LABELS[decoded]) return LABELS[decoded]
  // Ids: show a short, recognisable form until E1-04+ resolve names.
  if (/^[0-9a-f-]{8,}$/i.test(decoded)) return `#${decoded.slice(0, 8)}`
  if (/^\d+$/.test(decoded)) return `#${decoded}`
  return decoded.charAt(0).toUpperCase() + decoded.slice(1).replace(/-/g, " ")
}

/** Breadcrumbs derived from the URL's route segments. */
export function Breadcrumbs() {
  const pathname = usePathname()
  const segments = pathname.split("/").filter(Boolean)

  const crumbs = [
    { href: "/", label: "Home" },
    ...segments.map((segment, index) => ({
      href: `/${segments.slice(0, index + 1).join("/")}`,
      label: label(segment),
    })),
  ]

  return (
    <Breadcrumb>
      <BreadcrumbList>
        {crumbs.map((crumb, index) => {
          const last = index === crumbs.length - 1
          const linkable = crumb.href === "/" || LINKABLE.has(crumb.href)
          return (
            <React.Fragment key={crumb.href}>
              {index > 0 && <BreadcrumbSeparator />}
              <BreadcrumbItem>
                {last ? (
                  <BreadcrumbPage>{crumb.label}</BreadcrumbPage>
                ) : linkable ? (
                  <BreadcrumbLink asChild>
                    <Link href={crumb.href}>{crumb.label}</Link>
                  </BreadcrumbLink>
                ) : (
                  <span>{crumb.label}</span>
                )}
              </BreadcrumbItem>
            </React.Fragment>
          )
        })}
      </BreadcrumbList>
    </Breadcrumb>
  )
}
