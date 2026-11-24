import { InfoIcon } from "lucide-react"
import Link from "next/link"

import { personHref } from "@/lib/routes"

import { conflictMessage, type Person } from "./model"

/**
 * Unresolved identity conflicts (ADR 0004), read-only: a resume whose email
 * matched this Person and phone another (or the other way round). Reviewing
 * and merging them is a later epic (E6); this only tells the recruiter.
 */
export function ConflictsBanner({ person }: { person: Person }) {
  const open = person.conflicts.filter((conflict) => conflict.resolved_at === null)
  if (open.length === 0) return null
  return (
    <aside
      aria-label="Identity conflicts"
      className="flex gap-3 rounded-xl bg-canvas px-4 py-3.5 text-sm"
    >
      <InfoIcon className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
      <div className="flex flex-col gap-1.5">
        <p className="font-medium">This person may share a resume with someone else</p>
        <ul className="flex flex-col gap-1">
          {open.map((conflict) => (
            <li key={conflict.id}>
              {conflictMessage(conflict, person.applications)}{" "}
              <Link href={personHref(conflict.other_person_id)} className="text-link">
                View the other person
              </Link>
            </li>
          ))}
        </ul>
        <p className="text-xs text-muted-foreground">
          For your information. Merging people is not available yet.
        </p>
      </div>
    </aside>
  )
}
