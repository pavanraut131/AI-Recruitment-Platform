"use client"

import { MailIcon, UsersIcon } from "lucide-react"
import { useState } from "react"

import { RequirePermission } from "@/components/auth/no-access"
import { EmptyState, ErrorState } from "@/components/data-states"
import { Skeleton } from "@/components/ui/skeleton"
import { usePermissions } from "@/lib/auth/permissions"
import { useSignedInUser } from "@/lib/auth/session"

import { Pager } from "../pager"
import { INVITATIONS_PAGE_SIZE, MEMBERS_PAGE_SIZE, useInvitations, useMembers } from "../queries"
import { SettingsSection } from "../section"
import { InvitationList } from "./invitations"
import { InviteDialog } from "./invite-dialog"
import { countActiveOwners, grantableRoles, memberControls } from "./member-access"
import { MembersTable } from "./members-table"

/**
 * Settings → Team (EPIC-02 §9.3), for owners and admins (`team:read`): the
 * members with their roles, client access and status, and the open
 * invitations. Changing them needs `team:manage` (and
 * `team:manage_privileged` for owners and admins). Anyone else who opens the
 * URL gets the no-access state.
 */
export function TeamSettings() {
  return (
    <RequirePermission
      permission="team:read"
      what="see or manage the team"
      who="Owners and admins can."
      back={{ href: "/settings/profile", label: "Go to your profile" }}
    >
      <Team />
    </RequirePermission>
  )
}

function Team() {
  const me = useSignedInUser()
  const permissions = usePermissions()
  const [membersPage, setMembersPage] = useState(1)
  const [invitationsPage, setInvitationsPage] = useState(1)
  const members = useMembers(membersPage)
  const invitations = useInvitations(invitationsPage)
  const canInvite = permissions.can("team:manage")

  const total = members.data?.total
  return (
    <>
      <SettingsSection
        id="team-members-heading"
        title="Members"
        description={
          total === undefined
            ? "Everyone who works in this agency, and what they can do."
            : `${total === 1 ? "1 person works" : `${total} people work`} in this agency, deactivated members included.`
        }
        action={
          canInvite &&
          me?.active_agency && <InviteDialog roles={grantableRoles(permissions)} agencyName={me.active_agency.name} />
        }
      >
        {members.isPending ? (
          <MembersSkeleton />
        ) : members.isError ? (
          <ErrorState error={members.error} onRetry={() => void members.refetch()} retrying={members.isFetching} />
        ) : members.data.items.length === 0 ? (
          <EmptyState icon={UsersIcon} title="No members on this page" />
        ) : (
          <>
            <MembersTable
              members={members.data.items}
              controlsFor={(member) =>
                memberControls(member, {
                  userId: me?.user.id ?? "",
                  permissions,
                  activeOwners: countActiveOwners(members.data.items, members.data.total),
                })
              }
            />
            <Pager
              label="Member pages"
              page={membersPage}
              pageSize={MEMBERS_PAGE_SIZE}
              total={members.data.total}
              shown={members.data.items.length}
              noun="member"
              onPage={setMembersPage}
            />
          </>
        )}
      </SettingsSection>

      <SettingsSection
        id="team-invitations-heading"
        title="Pending invitations"
        description="Invitations nobody has accepted yet. A link works for 7 days; resending gives a new one."
      >
        {invitations.isPending ? (
          <MembersSkeleton rows={2} />
        ) : invitations.isError ? (
          <ErrorState
            error={invitations.error}
            onRetry={() => void invitations.refetch()}
            retrying={invitations.isFetching}
          />
        ) : invitations.data.total === 0 ? (
          <EmptyState
            icon={MailIcon}
            title="No pending invitations"
            description={canInvite ? "Invite a teammate and their invitation waits here until they accept it." : undefined}
            className="py-10"
          />
        ) : (
          <>
            <InvitationList invitations={invitations.data.items} permissions={permissions} />
            <Pager
              label="Invitation pages"
              page={invitationsPage}
              pageSize={INVITATIONS_PAGE_SIZE}
              total={invitations.data.total}
              shown={invitations.data.items.length}
              noun="invitation"
              onPage={setInvitationsPage}
            />
          </>
        )}
      </SettingsSection>
    </>
  )
}

function MembersSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="border-t" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="flex items-center gap-6 border-b px-1 py-3">
          <div className="flex w-56 flex-col gap-1.5">
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-3 w-52" />
          </div>
          <Skeleton className="h-7 w-32" />
          <Skeleton className="h-3.5 w-28" />
          <Skeleton className="ml-auto h-3.5 w-16" />
        </div>
      ))}
    </div>
  )
}
