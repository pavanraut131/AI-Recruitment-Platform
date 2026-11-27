import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import { toast } from "sonner"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { json, mockRoutes, pageOf, problem, writesOf } from "@/test/api"
import { installDomPolyfills } from "@/test/helpers"

import { ACME, FINCH, OLD_CO, renderSettings, teamInvitationFixture } from "../test-utils"
import { InviteDialog } from "./invite-dialog"

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const ALL_ROLES = ["owner", "admin", "recruiter", "viewer"] as const

function inviteApi(answer: () => Response = () => json(teamInvitationFixture({ email: "nia@finch.example" }), 201)) {
  return mockRoutes({
    "GET /clients": () => json(pageOf([ACME, FINCH, OLD_CO], { page_size: 100 })),
    "POST /team/invitations": answer,
  })
}

async function openDialog(roles: readonly (typeof ALL_ROLES)[number][] = ALL_ROLES) {
  renderSettings(<InviteDialog roles={roles} agencyName="Northbank Talent" />)
  fireEvent.click(screen.getByRole("button", { name: "Invite a teammate" }))
  return screen.findByRole("dialog", { name: "Invite a teammate" })
}

function fill(dialog: HTMLElement, email: string) {
  fireEvent.change(within(dialog).getByRole("textbox", { name: "Email" }), { target: { value: email } })
}

const send = (dialog: HTMLElement) => fireEvent.click(within(dialog).getByRole("button", { name: "Send invitation" }))

beforeEach(() => {
  installDomPolyfills()
  vi.mocked(toast.success).mockClear()
})

describe("InviteDialog", () => {
  it("invites a recruiter with every client by default, and says it was sent", async () => {
    const calls = inviteApi()
    const dialog = await openDialog()
    expect(dialog.textContent).toContain("They get an email with a link to join Northbank Talent. It works for 7 days.")
    expect(within(dialog).getByRole("radio", { name: "Recruiter" }).getAttribute("aria-checked")).toBe("true")
    expect(within(dialog).getByRole("radio", { name: "All clients" }).getAttribute("aria-checked")).toBe("true")

    fill(dialog, " nia@finch.example ")
    send(dialog)

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(writesOf(calls)[0].body).toEqual({ email: "nia@finch.example", role: "recruiter", all_clients: true, client_ids: [] })
    expect(toast.success).toHaveBeenCalledWith("Invitation sent to nia@finch.example", {
      description: "The link in it works for 7 days.",
    })
  })

  it("limits a viewer to the chosen active clients", async () => {
    const calls = inviteApi()
    const dialog = await openDialog()
    fill(dialog, "sam@finch.example")
    fireEvent.click(within(dialog).getByRole("radio", { name: "Viewer" }))
    fireEvent.click(within(dialog).getByRole("radio", { name: "Only some clients" }))

    const boxes = await within(dialog).findAllByRole("checkbox")
    // Archived clients are not offered.
    expect(boxes.map((box) => box.id && dialog.querySelector(`label[for="${box.id}"]`)?.textContent)).toEqual([
      "Acme Corp",
      "Finch Pay",
    ])
    send(dialog)
    expect(await within(dialog).findByText("Choose at least one client.")).toBeTruthy()
    expect(writesOf(calls)).toEqual([])

    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Finch Pay" }))
    send(dialog)
    await waitFor(() => expect(writesOf(calls)).toHaveLength(1))
    expect(writesOf(calls)[0].body).toEqual({
      email: "sam@finch.example",
      role: "viewer",
      all_clients: false,
      client_ids: [FINCH.id],
    })
  })

  it("gives owners and admins every client, without a client choice", async () => {
    const calls = inviteApi()
    const dialog = await openDialog()
    fill(dialog, "ada@finch.example")
    fireEvent.click(within(dialog).getByRole("radio", { name: "Only some clients" }))
    fireEvent.click(within(dialog).getByRole("radio", { name: "Admin" }))

    expect(within(dialog).getByText("Admins always see every client's campaigns.")).toBeTruthy()
    expect(within(dialog).queryByRole("radio", { name: "Only some clients" })).toBeNull()
    send(dialog)
    await waitFor(() => expect(writesOf(calls)).toHaveLength(1))
    expect(writesOf(calls)[0].body).toMatchObject({ role: "admin", all_clients: true, client_ids: [] })
  })

  it("offers an admin only the recruiter and viewer roles", async () => {
    inviteApi()
    const dialog = await openDialog(["recruiter", "viewer"])
    expect(within(dialog).getAllByRole("radio").slice(0, 2).map((radio) => radio.getAttribute("value"))).toEqual([
      "recruiter",
      "viewer",
    ])
    expect(within(dialog).queryByRole("radio", { name: "Owner" })).toBeNull()
  })

  it("checks the address before sending", async () => {
    const calls = inviteApi()
    const dialog = await openDialog()
    send(dialog)
    expect(await within(dialog).findByText("Enter their email address.")).toBeTruthy()
    fill(dialog, "not an address")
    send(dialog)
    expect(await within(dialog).findByText("Enter a valid email address.")).toBeTruthy()
    expect(writesOf(calls)).toEqual([])
  })

  it("puts an existing member's 409 under the address", async () => {
    inviteApi(() => problem(409, "nia@finch.example is already a member of this agency"))
    const dialog = await openDialog()
    fill(dialog, "nia@finch.example")
    send(dialog)

    expect(await within(dialog).findByText("nia@finch.example is already a member of this agency")).toBeTruthy()
    expect(within(dialog).getByRole("textbox", { name: "Email" }).getAttribute("aria-invalid")).toBe("true")
  })

  it("puts a 422's field errors under their fields", async () => {
    inviteApi(() => problem(422, "1 invalid field", { errors: [{ loc: ["body", "client_ids"], msg: "Unknown client" }] }))
    const dialog = await openDialog()
    fill(dialog, "sam@finch.example")
    fireEvent.click(within(dialog).getByRole("radio", { name: "Only some clients" }))
    fireEvent.click(await within(dialog).findByRole("checkbox", { name: "Acme Corp" }))
    send(dialog)

    expect(await within(dialog).findByText("Unknown client")).toBeTruthy()
    expect(screen.getByRole("dialog")).toBeTruthy()
  })

  it.each([
    [
      "429",
      () => problem(429, "Too many attempts. Try again later.", { headers: { "Retry-After": "5400" } }),
      "Too many invitations today",
      "Try again in 2 hours.",
    ],
    [
      "403",
      () => problem(403, "You don't have permission to manage owners and admins"),
      "You don't have permission to manage owners and admins",
      undefined,
    ],
    ["500", () => problem(500, "Boom"), "Something went wrong on our side", "Try again in a moment."],
  ])("says what happened above the buttons on a %s", async (_status, answer, title, description) => {
    inviteApi(answer)
    const dialog = await openDialog()
    fill(dialog, "sam@finch.example")
    send(dialog)

    const alert = await within(dialog).findByRole("alert")
    expect(alert.textContent).toContain(title)
    if (description) expect(alert.textContent).toContain(description)
    expect(screen.getByRole("dialog")).toBeTruthy()
  })
})
