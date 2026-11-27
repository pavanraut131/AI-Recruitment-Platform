import { describe, expect, it } from "vitest"

import { isAuthPath, loginHref, safeNextPath, selectAgencyHref } from "./paths"

describe("safeNextPath (no open redirect, spec §10)", () => {
  it("keeps a path on this site, with its query and fragment", () => {
    expect(safeNextPath("/campaigns/c-1?tab=uploads")).toBe("/campaigns/c-1?tab=uploads")
    expect(safeNextPath("/talent#top")).toBe("/talent#top")
    expect(safeNextPath("/")).toBe("/")
  })

  it.each([
    ["missing", undefined],
    ["empty", ""],
    ["an absolute URL", "https://evil.example/phish"],
    ["a protocol-relative URL", "//evil.example/phish"],
    ["a backslash trick", "/\\evil.example"],
    ["a relative path", "campaigns"],
    ["a javascript: URL", "javascript:alert(1)"],
    ["a sign-in page (a loop)", "/login?next=/campaigns"],
    ["another sign-in page", "/reset-password"],
  ])("falls back to / for %s", (_case, next) => {
    expect(safeNextPath(next)).toBe("/")
  })
})

describe("loginHref", () => {
  it("remembers where to come back to and why", () => {
    expect(loginHref({ next: "/campaigns/c-1?tab=uploads", reason: "expired" })).toBe(
      "/login?next=%2Fcampaigns%2Fc-1%3Ftab%3Duploads&reason=expired",
    )
  })

  it("is plain /login for Home, or for a next it would not follow", () => {
    expect(loginHref({ next: "/" })).toBe("/login")
    expect(loginHref({ next: "https://evil.example" })).toBe("/login")
    expect(loginHref()).toBe("/login")
  })
})

describe("selectAgencyHref", () => {
  it("goes on to next afterwards", () => {
    expect(selectAgencyHref("/talent")).toBe("/select-agency?next=%2Ftalent")
    expect(selectAgencyHref("/")).toBe("/select-agency")
  })
})

describe("isAuthPath", () => {
  it("is true for the pages a signed-out visitor uses, not for the agency picker or the app", () => {
    expect(["/login", "/forgot-password", "/reset-password", "/accept-invite"].every(isAuthPath)).toBe(true)
    expect(isAuthPath("/select-agency")).toBe(false)
    expect(isAuthPath("/")).toBe(false)
    expect(isAuthPath("/loginx")).toBe(false)
  })
})
