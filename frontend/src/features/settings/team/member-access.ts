import { PRIVILEGED_ROLES, type Permissions } from "@/lib/auth/permissions"
import { ROLES, type Role } from "@/lib/auth/roles"

import type { Member } from "../queries"

/*
 * What the signed-in user may change on a member's row, and why not (EPIC-02
 * §5.2 guard rails), so the table shows only working controls and says why a
 * row is read-only. The API enforces every rule: a 403 or 409 it returns
 * anyway (someone else changed the team meanwhile) is shown as it words it.
 */

export type MemberControls = {
  /** The signed-in user's own row. */
  isSelf: boolean
  /** The roles to offer in the role select (empty: the role can't be changed here). */
  roleOptions: Role[]
  /** "All clients" or a client list (recruiters and viewers only). */
  canEditClients: boolean
  canDeactivate: boolean
  canReactivate: boolean
  /** Why the row is (partly) read-only, as a sentence for under the role; `null` when nothing is held back. */
  reason: string | null
}

const LOCKED: Omit<MemberControls, "isSelf" | "reason"> = {
  roleOptions: [],
  canEditClients: false,
  canDeactivate: false,
  canReactivate: false,
}

/** Recruiters and viewers: the roles that can be limited to some clients (§5.3). */
export function isClientScopedRole(role: Role): boolean {
  return !PRIVILEGED_ROLES.includes(role)
}

/** The roles the signed-in user may give (`team:manage_privileged`: all of them; otherwise recruiter and viewer). */
export function grantableRoles(permissions: Permissions): Role[] {
  return ROLES.filter((role) => permissions.canManageRole(role))
}

export function memberControls(
  member: Member,
  {
    userId,
    permissions,
    activeOwners,
  }: {
    /** The signed-in user's id. */
    userId: string
    permissions: Permissions
    /** Active owners in the agency, when the whole team is loaded (`null`: unknown, the API decides). */
    activeOwners: number | null
  },
): MemberControls {
  const isSelf = member.user.id === userId
  const active = member.status === "active"
  const lastOwner = member.role === "owner" && active && activeOwners === 1
  if (!permissions.can("team:manage")) {
    return { ...LOCKED, isSelf, reason: null }
  }
  if (isSelf) {
    // Both guard rails at once for the only owner, who can never be changed from this table.
    const reason = lastOwner
      ? "You're the agency's only owner, and you can't change your own role or access."
      : "You can't change your own role or access."
    return { ...LOCKED, isSelf, reason }
  }
  if (!permissions.canManageRole(member.role)) {
    return { ...LOCKED, isSelf, reason: "Only owners can change owners and admins." }
  }
  // Only reachable when the list is stale (another owner left meanwhile); the API answers 409 anyway.
  if (lastOwner) {
    return {
      ...LOCKED,
      isSelf,
      reason: "The agency's only owner. Make someone else an owner first.",
    }
  }
  return {
    isSelf,
    roleOptions: grantableRoles(permissions),
    canEditClients: isClientScopedRole(member.role),
    canDeactivate: active,
    canReactivate: !active,
    reason: null,
  }
}

/** The active owners on a fully loaded team list, else `null` (more pages: count unknown). */
export function countActiveOwners(members: readonly Member[], total: number): number | null {
  if (members.length < total) return null
  return members.filter((member) => member.role === "owner" && member.status === "active").length
}

/** A member's or invitation's client access in words: "All clients", or the clients by name. */
export function clientAccessText(access: { all_clients: boolean; clients: readonly { name: string }[] }): string {
  if (access.all_clients) return "All clients"
  if (access.clients.length === 0) return "No clients"
  return access.clients.map((client) => client.name).join(", ")
}
