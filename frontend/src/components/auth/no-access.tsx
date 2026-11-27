"use client"

import Link from "next/link"
import { LockIcon } from "lucide-react"

import { EmptyState } from "@/components/data-states"
import { Button } from "@/components/ui/button"
import { usePermissions, type Permission } from "@/lib/auth/permissions"
import { ROLE_IN_SENTENCE } from "@/lib/auth/roles"
import { useSignedInUser } from "@/lib/auth/session"
import { cn } from "@/lib/utils"

/**
 * A page the signed-in role cannot use, opened by its URL (EPIC-02 §9.2): a
 * viewer on `/campaigns/new`, a recruiter on `/settings/team`. It says so,
 * names the role, and offers a way back. The API refuses the requests anyway
 * (403); this only saves a round of errors.
 */
export function NoAccess({
  what,
  who,
  back = { href: "/", label: "Go to Home" },
  className,
}: {
  /** What the page is for, as an object of "can't": "invite or manage the team". */
  what: string
  /** Who can, as a sentence: "Owners and admins can." */
  who: string
  back?: { href: string; label: string }
  className?: string
}) {
  const me = useSignedInUser()
  const role = me?.role ? ROLE_IN_SENTENCE[me.role] : null
  const agency = me?.active_agency?.name
  return (
    <EmptyState
      icon={LockIcon}
      title="You don't have access to this page"
      className={cn("border-t-0", className)}
      description={
        <>
          {role && agency ? `As ${role} in ${agency}, you can't ${what}. ` : `You can't ${what}. `}
          {who} Ask one of them if you need it.
        </>
      }
      action={
        <Button variant="outline" asChild>
          <Link href={back.href}>{back.label}</Link>
        </Button>
      }
    />
  )
}

/**
 * Renders `children` only for a role with `permission`; otherwise the
 * no-access state. For pages a URL can reach (spec §9.2: "a direct URL to a
 * write action gets the no-access state").
 */
export function RequirePermission({
  permission,
  children,
  ...noAccess
}: {
  permission: Permission
  children: React.ReactNode
} & React.ComponentProps<typeof NoAccess>) {
  const { can } = usePermissions()
  if (!can(permission)) return <NoAccess {...noAccess} />
  return children
}
