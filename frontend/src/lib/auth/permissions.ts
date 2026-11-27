"use client"

import { useMemo } from "react"

import type { Schema } from "@/lib/api/client"

import type { Role } from "./roles"
import { useSignedInUser, type Me } from "./session"

/*
 * What the signed-in user may do in the active agency (EPIC-02 §5.2, §9.2):
 * `Me.permissions`, the role's row of the permission matrix. The UI only
 * reflects it, hiding what the role cannot do; the API checks every request
 * anyway, so nothing here is a security boundary.
 */

export type Permission = Schema<"Permission">

/** Owners and admins: they always see every client, and only owners manage them (§5.2). */
export const PRIVILEGED_ROLES: readonly Role[] = ["owner", "admin"]

export type Permissions = {
  role: Role | null
  /** Whether the role has `permission` in the active agency. */
  can: (permission: Permission) => boolean
  /**
   * Whether the user may manage a member or invitation with `role` (§5.2:
   * `team:manage` for recruiters and viewers, `team:manage_privileged` for
   * owners and admins).
   */
  canManageRole: (role: Role) => boolean
}

const NONE: readonly Permission[] = []

/** The permissions of a signed-in user (none before `/me` has loaded). */
export function permissionsOf(me: Me | undefined): Permissions {
  const granted = new Set(me?.permissions ?? NONE)
  const can = (permission: Permission) => granted.has(permission)
  return {
    role: me?.role ?? null,
    can,
    canManageRole: (role) => can(PRIVILEGED_ROLES.includes(role) ? "team:manage_privileged" : "team:manage"),
  }
}

/** The signed-in user's permissions in the active agency, from the `Me` the auth gate loaded. */
export function usePermissions(): Permissions {
  const me = useSignedInUser()
  return useMemo(() => permissionsOf(me), [me])
}
