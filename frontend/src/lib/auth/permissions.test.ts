import { describe, expect, it } from "vitest"

import { meAs } from "@/test/auth"

import { permissionsOf } from "./permissions"

describe("permissionsOf", () => {
  it("reads the role's permissions from Me", () => {
    const viewer = permissionsOf(meAs("viewer"))
    expect(viewer.role).toBe("viewer")
    expect(viewer.can("workspace:read")).toBe(true)
    expect(viewer.can("campaign:write")).toBe(false)
    expect(viewer.can("application:export")).toBe(false)

    const recruiter = permissionsOf(meAs("recruiter"))
    expect(recruiter.can("campaign:write")).toBe(true)
    expect(recruiter.can("campaign:delete")).toBe(false)
    expect(recruiter.can("team:read")).toBe(false)
  })

  it("has nothing before /me has loaded", () => {
    const none = permissionsOf(undefined)
    expect(none.role).toBeNull()
    expect(none.can("workspace:read")).toBe(false)
    expect(none.canManageRole("viewer")).toBe(false)
  })

  it("lets owners manage every role and admins only recruiters and viewers (§5.2)", () => {
    const owner = permissionsOf(meAs("owner"))
    expect(["owner", "admin", "recruiter", "viewer"].map((role) => owner.canManageRole(role as never))).toEqual([
      true,
      true,
      true,
      true,
    ])
    const admin = permissionsOf(meAs("admin"))
    expect(["owner", "admin", "recruiter", "viewer"].map((role) => admin.canManageRole(role as never))).toEqual([
      false,
      false,
      true,
      true,
    ])
    expect(permissionsOf(meAs("recruiter")).canManageRole("viewer")).toBe(false)
  })
})
