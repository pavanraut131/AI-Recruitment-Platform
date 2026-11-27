import { describe, expect, it } from "vitest"

import { permissionsOf } from "@/lib/auth/permissions"
import { meAs } from "@/test/auth"

import { ME, memberFixture, selfMember } from "../test-utils"
import { clientAccessText, countActiveOwners, grantableRoles, memberControls } from "./member-access"

const owner = { userId: ME.id, permissions: permissionsOf(meAs("owner")), activeOwners: 2 }
const admin = { userId: ME.id, permissions: permissionsOf(meAs("admin")), activeOwners: 2 }

describe("memberControls", () => {
  it("lets an owner change any other member: every role, a recruiter's clients, deactivate", () => {
    const controls = memberControls(memberFixture({ role: "recruiter" }), owner)
    expect(controls).toEqual({
      isSelf: false,
      roleOptions: ["owner", "admin", "recruiter", "viewer"],
      canEditClients: true,
      canDeactivate: true,
      canReactivate: false,
      reason: null,
    })
    // Owners and admins always have every client: nothing to edit.
    expect(memberControls(memberFixture({ role: "admin" }), owner).canEditClients).toBe(false)
  })

  it("never lets anyone change their own row", () => {
    expect(memberControls(selfMember(), owner)).toMatchObject({
      isSelf: true,
      roleOptions: [],
      canDeactivate: false,
      reason: "You can't change your own role or access.",
    })
    expect(memberControls(selfMember(), { ...owner, activeOwners: 1 }).reason).toBe(
      "You're the agency's only owner, and you can't change your own role or access.",
    )
  })

  it("keeps owners and admins out of an admin's reach, and offers them recruiter and viewer only", () => {
    expect(memberControls(memberFixture({ role: "owner" }), admin)).toMatchObject({
      roleOptions: [],
      canDeactivate: false,
      reason: "Only owners can change owners and admins.",
    })
    expect(memberControls(memberFixture({ role: "admin" }), admin).reason).toBe("Only owners can change owners and admins.")
    expect(memberControls(memberFixture({ role: "viewer" }), admin).roleOptions).toEqual(["recruiter", "viewer"])
  })

  it("holds back the last active owner", () => {
    expect(memberControls(memberFixture({ role: "owner" }), { ...owner, activeOwners: 1 })).toMatchObject({
      roleOptions: [],
      canDeactivate: false,
      reason: "The agency's only owner. Make someone else an owner first.",
    })
    // Unknown (the team spans pages): the API decides.
    expect(memberControls(memberFixture({ role: "owner" }), { ...owner, activeOwners: null }).canDeactivate).toBe(true)
  })

  it("offers Reactivate for a deactivated member", () => {
    expect(memberControls(memberFixture({ status: "deactivated" }), owner)).toMatchObject({
      canDeactivate: false,
      canReactivate: true,
    })
  })

  it("changes nothing for a role without team:manage", () => {
    const recruiter = { userId: ME.id, permissions: permissionsOf(meAs("recruiter")), activeOwners: 1 }
    expect(memberControls(memberFixture(), recruiter)).toMatchObject({ roleOptions: [], canEditClients: false, reason: null })
  })
})

describe("the team helpers", () => {
  it("counts active owners only on a complete list", () => {
    const team = [memberFixture({ role: "owner" }), memberFixture({ role: "owner", status: "deactivated" }), memberFixture()]
    expect(countActiveOwners(team, 3)).toBe(1)
    expect(countActiveOwners(team, 120)).toBeNull()
  })

  it("lists the roles a user may give", () => {
    expect(grantableRoles(permissionsOf(meAs("owner")))).toEqual(["owner", "admin", "recruiter", "viewer"])
    expect(grantableRoles(permissionsOf(meAs("admin")))).toEqual(["recruiter", "viewer"])
  })

  it("words client access", () => {
    expect(clientAccessText({ all_clients: true, clients: [] })).toBe("All clients")
    expect(clientAccessText({ all_clients: false, clients: [{ name: "Acme Corp" }, { name: "Finch Pay" }] })).toBe(
      "Acme Corp, Finch Pay",
    )
    expect(clientAccessText({ all_clients: false, clients: [] })).toBe("No clients")
  })
})
