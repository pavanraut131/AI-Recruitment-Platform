import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import { toast } from "sonner"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { json, mockRoutes, noContent, pageOf, problem, writesOf } from "@/test/api"
import { meAs } from "@/test/auth"
import { installDomPolyfills } from "@/test/helpers"

import { ACME, FINCH, ME, memberFixture, OLD_CO, renderSettings, selfMember, teamInvitationFixture } from "../test-utils"
import { TeamSettings } from "./team-settings"

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const ADMIN = memberFixture({ name: "Ada Admin", email: "ada@northbank.example", role: "admin", last_active_at: "2026-11-27T09:00:00Z" })
const RECRUITER = memberFixture({
  name: "Nia Okafor",
  email: "nia@northbank.example",
  role: "recruiter",
  all_clients: false,
  clients: [{ id: ACME.id, name: ACME.name }],
})
const VIEWER = memberFixture({
  name: "Vik Viewer",
  email: "vik@northbank.example",
  role: "viewer",
  status: "deactivated",
  deactivated_at: "2026-11-20T09:00:00Z",
})
const SELF = selfMember()

const PENDING = teamInvitationFixture({
  email: "sam@northbank.example",
  role: "viewer",
  all_clients: false,
  clients: [{ id: FINCH.id, name: FINCH.name }],
})
const EXPIRED = teamInvitationFixture({ email: "old@northbank.example", role: "admin", status: "expired", expires_at: "2026-11-01T09:00:00Z" })

function teamApi(extra: Parameters<typeof mockRoutes>[0] = {}) {
  return mockRoutes({
    "GET /team/members": () => json(pageOf([ADMIN, RECRUITER, SELF, VIEWER], { page_size: 100 })),
    "GET /team/invitations": () => json(pageOf([PENDING, EXPIRED])),
    "GET /clients": () => json(pageOf([ACME, FINCH, OLD_CO], { page_size: 100 })),
    ...extra,
  })
}

function row(name: string) {
  return screen.getAllByRole("row").find((element) => element.textContent?.includes(name))!
}

function openMenu(trigger: HTMLElement) {
  fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false })
  return screen.findByRole("menu")
}

async function chooseOption(combobox: HTMLElement, name: string) {
  fireEvent.click(combobox)
  fireEvent.click(await screen.findByRole("option", { name }))
}

beforeEach(() => {
  installDomPolyfills()
  vi.mocked(toast.success).mockClear()
  vi.mocked(toast.error).mockClear()
})

describe("Settings → Team: members", () => {
  it("lists members with their role, client access, status and last activity", async () => {
    teamApi()
    renderSettings(<TeamSettings />)

    await screen.findByRole("table")
    expect(screen.getByText("4 people work in this agency.", { exact: false })).toBeTruthy()
    const nia = within(row("Nia Okafor"))
    expect(nia.getByText("nia@northbank.example")).toBeTruthy()
    expect(nia.getByRole("combobox", { name: "Role of Nia Okafor" }).textContent).toBe("Recruiter")
    expect(nia.getByText("Acme Corp")).toBeTruthy()
    expect(nia.getByText("Active")).toBeTruthy()
    expect(nia.getByText("Never")).toBeTruthy()
    const vik = within(row("Vik Viewer"))
    expect(vik.getByText("Deactivated")).toBeTruthy()
    expect(within(row("Ada Admin")).getByText("All clients")).toBeTruthy()
  })

  it("explains the guard rails: your own row, the only owner, and (for admins) owners and admins", async () => {
    teamApi()
    renderSettings(<TeamSettings />)

    const self = within(await waitFor(() => row(ME.display_name)))
    expect(self.getByText("You")).toBeTruthy()
    expect(self.queryByRole("combobox")).toBeNull()
    expect(self.getByText("You're the agency's only owner, and you can't change your own role or access.")).toBeTruthy()
    expect(self.queryByRole("button", { name: /Actions for/ })).toBeNull()
  })

  it("gives an admin read-only owner and admin rows, and only the recruiter and viewer roles", async () => {
    const OWNER = memberFixture({ name: "Olu Owner", role: "owner" })
    const adminMe = meAs("admin", { user: ADMIN.user })
    mockRoutes({
      "GET /team/members": () => json(pageOf([ADMIN, OWNER, RECRUITER], { page_size: 100 })),
      "GET /team/invitations": () => json(pageOf([EXPIRED])),
      "GET /clients": () => json(pageOf([ACME])),
    })
    renderSettings(<TeamSettings />, { me: adminMe })

    const owner = within(await waitFor(() => row("Olu Owner")))
    expect(owner.queryByRole("combobox")).toBeNull()
    expect(owner.getByText("Only owners can change owners and admins.")).toBeTruthy()
    expect(within(row("Ada Admin")).getByText("You can't change your own role or access.")).toBeTruthy()

    fireEvent.click(within(row("Nia Okafor")).getByRole("combobox", { name: "Role of Nia Okafor" }))
    expect((await screen.findAllByRole("option")).map((option) => option.textContent)).toEqual(["Recruiter", "Viewer"])
    fireEvent.keyDown(document.activeElement!, { key: "Escape" })

    // An admin's invitation (owners' business) has no Resend or Revoke.
    const invitations = screen.getByRole("list", { name: "Pending invitations" })
    expect(within(invitations).queryByRole("button", { name: /Resend/ })).toBeNull()
  })

  it("changes a role at once and says so", async () => {
    const calls = teamApi({ "PATCH /team/members/{id}": () => json({ ...RECRUITER, role: "admin" }) })
    renderSettings(<TeamSettings />)

    await chooseOption(await screen.findByRole("combobox", { name: "Role of Nia Okafor" }), "Admin")

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Nia Okafor is now an admin"))
    expect(writesOf(calls)).toMatchObject([{ method: "PATCH", path: `/team/members/${RECRUITER.id}`, body: { role: "admin" } }])
  })

  it.each([
    [409, "The agency needs at least one active owner"],
    [403, "You don't have permission to manage owners and admins"],
  ])("shows the API's %i when a role change is refused", async (status, detail) => {
    teamApi({ "PATCH /team/members/{id}": () => problem(status, detail) })
    renderSettings(<TeamSettings />)

    await chooseOption(await screen.findByRole("combobox", { name: "Role of Nia Okafor" }), "Viewer")

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Nia Okafor's role wasn't changed", { description: detail }))
  })

  it("changes a recruiter's client access to some clients, needing at least one", async () => {
    const calls = teamApi({ "PATCH /team/members/{id}": () => json(RECRUITER) })
    renderSettings(<TeamSettings />)

    const menu = await openMenu(await screen.findByRole("button", { name: "Actions for Nia Okafor" }))
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Change client access…" }))
    const dialog = await screen.findByRole("dialog", { name: "Client access for Nia Okafor" })
    // Archived clients are not offered; the current one is checked.
    expect(within(dialog).getByRole("radio", { name: "Only some clients" }).getAttribute("aria-checked")).toBe("true")
    expect(await within(dialog).findAllByRole("checkbox")).toHaveLength(2)
    const acme = within(dialog).getByRole("checkbox", { name: "Acme Corp" })
    expect(acme.getAttribute("aria-checked")).toBe("true")

    fireEvent.click(acme)
    fireEvent.click(within(dialog).getByRole("button", { name: "Save access" }))
    expect(await within(dialog).findByText("Choose at least one client.")).toBeTruthy()
    expect(writesOf(calls)).toEqual([])

    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Finch Pay" }))
    fireEvent.click(within(dialog).getByRole("button", { name: "Save access" }))
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(writesOf(calls)[0].body).toEqual({ all_clients: false, client_ids: [FINCH.id] })
    expect(toast.success).toHaveBeenCalledWith("Client access saved for Nia Okafor")
  })

  it("deactivates after a confirmation, and reactivates", async () => {
    const calls = teamApi({
      "POST /team/members/{id}/deactivate": () => json({ ...RECRUITER, status: "deactivated" }),
      "POST /team/members/{id}/reactivate": () => json({ ...VIEWER, status: "active" }),
    })
    renderSettings(<TeamSettings />)

    let menu = await openMenu(await screen.findByRole("button", { name: "Actions for Nia Okafor" }))
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Deactivate…" }))
    const confirm = await screen.findByRole("alertdialog", { name: "Deactivate Nia Okafor?" })
    expect(writesOf(calls)).toEqual([])
    fireEvent.click(within(confirm).getByRole("button", { name: "Deactivate" }))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Nia Okafor is deactivated", expect.anything()))
    expect(writesOf(calls).map((call) => call.path)).toEqual([`/team/members/${RECRUITER.id}/deactivate`])

    menu = await openMenu(screen.getByRole("button", { name: "Actions for Vik Viewer" }))
    expect(within(menu).queryByRole("menuitem", { name: "Deactivate…" })).toBeNull()
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Reactivate" }))
    await waitFor(() => expect(writesOf(calls).at(-1)?.path).toBe(`/team/members/${VIEWER.id}/reactivate`))
  })

  it("shows the API's 409 when deactivating the last owner is refused", async () => {
    const OTHER_OWNER = memberFixture({ name: "Olu Owner", role: "owner" })
    mockRoutes({
      "GET /team/members": () => json(pageOf([SELF, OTHER_OWNER], { page_size: 100 })),
      "GET /team/invitations": () => json(pageOf([])),
      "GET /clients": () => json(pageOf([])),
      "POST /team/members/{id}/deactivate": () => problem(409, "The agency needs at least one active owner"),
    })
    renderSettings(<TeamSettings />)

    const menu = await openMenu(await screen.findByRole("button", { name: "Actions for Olu Owner" }))
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Deactivate…" }))
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Deactivate" }))

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("Olu Owner wasn't deactivated", {
        description: "The agency needs at least one active owner",
      }),
    )
  })

  it("shows a recruiter or viewer who opens the URL the no-access state", () => {
    const calls = teamApi()
    renderSettings(<TeamSettings />, { me: meAs("recruiter") })

    expect(screen.getByRole("heading", { name: "You don't have access to this page" })).toBeTruthy()
    expect(screen.getByText(/you can't see or manage the team\. Owners and admins can\./)).toBeTruthy()
    expect(screen.getByRole("link", { name: "Go to your profile" }).getAttribute("href")).toBe("/settings/profile")
    expect(calls).toEqual([])
  })
})

describe("Settings → Team: invitations", () => {
  it("lists open invitations with role, clients, sender and expiry", async () => {
    teamApi()
    renderSettings(<TeamSettings />)

    const list = await screen.findByRole("list", { name: "Pending invitations" })
    const [sam, old] = within(list).getAllByRole("listitem")
    expect(sam.textContent).toContain("sam@northbank.example")
    expect(sam.textContent).toContain(`Viewer · Finch Pay · Sent by ${ME.display_name} · expires in`)
    expect(within(old).getByText("Expired")).toBeTruthy()
    expect(old.textContent).toMatch(/expired .* ago/)
  })

  it("resends an invitation, and says why when it can't", async () => {
    let attempts = 0
    const calls = teamApi({
      "POST /team/invitations/{id}/resend": () => {
        attempts += 1
        return attempts === 1
          ? json(PENDING)
          : problem(429, "Too many attempts. Try again later.", { headers: { "Retry-After": "7200" } })
      },
    })
    renderSettings(<TeamSettings />)

    const resend = await screen.findByRole("button", { name: "Resend the invitation to sam@northbank.example" })
    fireEvent.click(resend)
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Invitation sent again to sam@northbank.example", expect.anything()))
    expect(writesOf(calls)[0].path).toBe(`/team/invitations/${PENDING.id}/resend`)

    fireEvent.click(resend)
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("The invitation wasn't sent", {
        description: "Too many invitations today. Try again in 2 hours.",
      }),
    )
  })

  it("revokes an invitation after a confirmation", async () => {
    const calls = teamApi({ "DELETE /team/invitations/{id}": () => noContent() })
    renderSettings(<TeamSettings />)

    fireEvent.click(await screen.findByRole("button", { name: "Revoke the invitation to old@northbank.example" }))
    const confirm = await screen.findByRole("alertdialog")
    expect(confirm.textContent).toContain("The link in their email stops working.")
    fireEvent.click(within(confirm).getByRole("button", { name: "Revoke invitation" }))

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Invitation for old@northbank.example revoked"))
    expect(writesOf(calls)).toMatchObject([{ method: "DELETE", path: `/team/invitations/${EXPIRED.id}` }])
  })

  it("says when there are none", async () => {
    mockRoutes({
      "GET /team/members": () => json(pageOf([SELF], { page_size: 100 })),
      "GET /team/invitations": () => json(pageOf([])),
      "GET /clients": () => json(pageOf([])),
    })
    renderSettings(<TeamSettings />)

    expect(await screen.findByText("No pending invitations")).toBeTruthy()
  })
})

describe("Settings → Team: client access and archived clients", () => {
  it("drops a client that was archived since, and says so before saving", async () => {
    const LIMITED = memberFixture({
      name: "Ola Limited",
      role: "viewer",
      all_clients: false,
      clients: [
        { id: ACME.id, name: ACME.name },
        { id: OLD_CO.id, name: OLD_CO.name },
      ],
    })
    const calls = mockRoutes({
      "GET /team/members": () => json(pageOf([SELF, LIMITED], { page_size: 100 })),
      "GET /team/invitations": () => json(pageOf([])),
      "GET /clients": () => json(pageOf([ACME, FINCH, OLD_CO], { page_size: 100 })),
      "PATCH /team/members/{id}": () => json(LIMITED),
    })
    renderSettings(<TeamSettings />)

    const menu = await openMenu(await screen.findByRole("button", { name: "Actions for Ola Limited" }))
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Change client access…" }))
    const dialog = await screen.findByRole("dialog", { name: "Client access for Ola Limited" })
    expect((await within(dialog).findByRole("status")).textContent).toBe(
      "Old Co is archived, so saving removes it from Ola Limited's access.",
    )
    fireEvent.click(within(dialog).getByRole("button", { name: "Save access" }))

    await waitFor(() => expect(writesOf(calls)).toHaveLength(1))
    expect(writesOf(calls)[0].body).toEqual({ all_clients: false, client_ids: [ACME.id] })
  })
})
