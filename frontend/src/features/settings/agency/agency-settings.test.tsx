import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import { toast } from "sonner"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { queryKeys } from "@/lib/api/query-keys"
import { json, mockRoutes, problem, writesOf } from "@/test/api"
import { meAs } from "@/test/auth"

import { renderSettings } from "../test-utils"
import { AgencySettings } from "./agency-settings"

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const AGENCY = { id: "a0000000-0000-4000-8000-000000000001", name: "Northbank Talent", slug: "northbank" }

beforeEach(() => {
  vi.mocked(toast.success).mockClear()
})

describe("Settings → Agency", () => {
  it("renames the agency; its short name stays", async () => {
    const calls = mockRoutes({
      "GET /agency": () => json(AGENCY),
      "PATCH /agency": () => json({ ...AGENCY, name: "Northbank Talent Ltd" }),
    })
    const { queryClient } = renderSettings(<AgencySettings />)

    const form = await screen.findByRole("form", { name: "Agency" })
    expect(within(form).getByText("northbank")).toBeTruthy()
    fireEvent.change(within(form).getByRole("textbox", { name: "Name" }), { target: { value: "Northbank Talent Ltd" } })
    fireEvent.click(within(form).getByRole("button", { name: "Save name" }))

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Agency renamed", { description: "Northbank Talent Ltd" }))
    expect(writesOf(calls)[0].body).toEqual({ name: "Northbank Talent Ltd" })
    // The switcher and the user menu show the name from /me: fetched again.
    expect(queryClient.getQueryState(queryKeys.me.all)?.isInvalidated).toBe(true)
  })

  it("puts a 422 under the name", async () => {
    mockRoutes({
      "GET /agency": () => json(AGENCY),
      "PATCH /agency": () => problem(422, "1 invalid field", { errors: [{ loc: ["body", "name"], msg: "Too long" }] }),
    })
    renderSettings(<AgencySettings />)
    const form = await screen.findByRole("form", { name: "Agency" })
    fireEvent.change(within(form).getByRole("textbox", { name: "Name" }), { target: { value: "X" } })
    fireEvent.click(within(form).getByRole("button", { name: "Save name" }))

    expect(await within(form).findByText("Too long")).toBeTruthy()
  })

  it("is for owners: an admin who opens the URL gets the no-access state", () => {
    const calls = mockRoutes({})
    renderSettings(<AgencySettings />, { me: meAs("admin") })

    expect(screen.getByRole("heading", { name: "You don't have access to this page" })).toBeTruthy()
    expect(screen.getByText(/As an admin in Northbank Talent, you can't change the agency's settings\. Owners can\./)).toBeTruthy()
    expect(calls).toEqual([])
  })
})
