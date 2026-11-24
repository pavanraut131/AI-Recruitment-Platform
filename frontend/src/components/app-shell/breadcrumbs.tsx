"use client"

import * as React from "react"
import { skipToken, useQuery } from "@tanstack/react-query"
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
import type { Schema } from "@/lib/api/client"
import { queryKeys } from "@/lib/api/query-keys"
import { displayName } from "@/lib/applications"

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

/** Segments whose index lives elsewhere: people are listed on the Talent page. */
const INDEX_HREF: Record<string, string> = { "/people": "/talent" }

const ID = /^[0-9a-f-]{8,}$/i

function label(segment: string) {
  const decoded = decodeURIComponent(segment)
  if (LABELS[decoded]) return LABELS[decoded]
  // Ids the cache cannot name (yet): a short, recognisable form.
  if (ID.test(decoded)) return `#${decoded.slice(0, 8)}`
  if (/^\d+$/.test(decoded)) return `#${decoded}`
  return decoded.charAt(0).toUpperCase() + decoded.slice(1).replace(/-/g, " ")
}

/**
 * The name of the campaign or person a route is about, read from the Query
 * cache the page itself fills (`skipToken`: this never fetches), so the
 * breadcrumb says "Senior Python Engineer" instead of an id once the page has
 * loaded it.
 */
function useEntityName(segments: string[]): string | undefined {
  const [root, id] = segments
  const kind = id && ID.test(id) ? root : undefined
  const campaign = useQuery<Schema<"Campaign">>({
    queryKey: queryKeys.campaigns.detail(id ?? ""),
    queryFn: skipToken,
    enabled: kind === "campaigns",
  })
  const person = useQuery<Schema<"Person">>({
    queryKey: queryKeys.people.detail(id ?? ""),
    queryFn: skipToken,
    enabled: kind === "people",
  })
  if (kind === "campaigns") return campaign.data?.title
  if (kind === "people") return person.data ? displayName(person.data.name) : undefined
  return undefined
}

/** Breadcrumbs derived from the URL's route segments; none on Home, which is the root. */
export function Breadcrumbs() {
  const pathname = usePathname()
  const segments = pathname.split("/").filter(Boolean)
  const entityName = useEntityName(segments)

  if (segments.length === 0) return null

  const crumbs = [
    { href: "/", label: "Home" },
    ...segments.map((segment, index) => ({
      href: `/${segments.slice(0, index + 1).join("/")}`,
      label: index === 1 && entityName ? entityName : label(segment),
    })),
  ]

  return (
    <Breadcrumb className="min-w-0">
      <BreadcrumbList className="flex-nowrap text-dense">
        {crumbs.map((crumb, index) => {
          const last = index === crumbs.length - 1
          const linkable = crumb.href === "/" || LINKABLE.has(crumb.href)
          return (
            <React.Fragment key={crumb.href}>
              {index > 0 && <BreadcrumbSeparator />}
              <BreadcrumbItem className={last ? "min-w-0" : "shrink-0"}>
                {last ? (
                  <BreadcrumbPage className="truncate">{crumb.label}</BreadcrumbPage>
                ) : linkable ? (
                  <BreadcrumbLink asChild>
                    <Link href={INDEX_HREF[crumb.href] ?? crumb.href}>{crumb.label}</Link>
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
