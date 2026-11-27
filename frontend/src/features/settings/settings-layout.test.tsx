import { screen, within } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { meAs } from "@/test/auth"

import { SettingsLayout } from "./settings-layout"
import { renderSettings } from "./test-utils"

const { pathname } = vi.hoisted(() => ({ pathname: { current: "/settings/profile" } }))
vi.mock("next/navigation", () => ({ usePathname: () => pathname.current }))

function tabs() {
  return within(screen.getByRole("navigation", { name: "Settings sections" }))
    .getAllByRole("link")
    .map((link) => link.textContent)
}

describe("SettingsLayout", () => {
  it.each([
    ["owner", ["Profile", "Team", "Clients", "Agency"]],
    ["admin", ["Profile", "Team", "Clients"]],
    ["recruiter", ["Profile"]],
    ["viewer", ["Profile"]],
  ] as const)("shows a %s the sections their role can use", (role, expected) => {
    renderSettings(<SettingsLayout>content</SettingsLayout>, { me: meAs(role) })
    expect(tabs()).toEqual(expected)
  })

  it("marks the current section", () => {
    pathname.current = "/settings/team"
    renderSettings(<SettingsLayout>content</SettingsLayout>)
    const nav = within(screen.getByRole("navigation", { name: "Settings sections" }))
    expect(nav.getByRole("link", { name: "Team" }).getAttribute("aria-current")).toBe("page")
    expect(nav.getByRole("link", { name: "Profile" }).getAttribute("aria-current")).toBeNull()
  })
})
