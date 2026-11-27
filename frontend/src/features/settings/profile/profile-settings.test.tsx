import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import { toast } from "sonner"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { queryKeys } from "@/lib/api/query-keys"
import { json, mockRoutes, noContent, problem, writesOf } from "@/test/api"
import { meAs } from "@/test/auth"

import { renderSettings } from "../test-utils"
import { ProfileSettings } from "./profile-settings"

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const ME = meAs("recruiter")

function nameForm() {
  return screen.getByRole("form", { name: "Your profile" })
}
function passwordForm() {
  return screen.getByRole("form", { name: "Change password" })
}
function fillPasswords(form: HTMLElement, current: string, next: string, again = next) {
  fireEvent.change(within(form).getByLabelText("Current password"), { target: { value: current } })
  fireEvent.change(within(form).getByLabelText("New password"), { target: { value: next } })
  fireEvent.change(within(form).getByLabelText("New password, again"), { target: { value: again } })
}

beforeEach(() => {
  vi.mocked(toast.success).mockClear()
})

describe("Settings → Profile", () => {
  it("shows the name to edit, the role, and the email as read-only", () => {
    mockRoutes({})
    renderSettings(<ProfileSettings />, { me: ME })

    expect(screen.getByText("You're a recruiter in Northbank Talent.")).toBeTruthy()
    expect(within(nameForm()).getByRole("textbox", { name: "Name" })).toHaveProperty("value", "Priya Shah")
    expect(screen.getByText("priya@northbank.example")).toBeTruthy()
    expect(screen.getByText("You sign in with this address. It can't be changed here.")).toBeTruthy()
    expect(screen.queryByRole("textbox", { name: "Email" })).toBeNull()
    // Nothing to save until the name changes.
    expect(within(nameForm()).getByRole("button", { name: "Save name" }).hasAttribute("disabled")).toBe(true)
  })

  it("saves the name, which shows at once wherever the user is shown", async () => {
    const saved = { ...ME, user: { ...ME.user, display_name: "Priya S." } }
    const calls = mockRoutes({ "PATCH /me": () => json(saved) })
    const { queryClient } = renderSettings(<ProfileSettings />, { me: ME })

    fireEvent.change(within(nameForm()).getByRole("textbox", { name: "Name" }), { target: { value: "  Priya S. " } })
    fireEvent.click(within(nameForm()).getByRole("button", { name: "Save name" }))

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Name saved"))
    expect(writesOf(calls)).toMatchObject([{ method: "PATCH", path: "/me", body: { display_name: "Priya S." } }])
    expect(queryClient.getQueryData<typeof ME>(queryKeys.me.all)?.user.display_name).toBe("Priya S.")
  })

  it("puts a 422 on the name under it", async () => {
    mockRoutes({
      "PATCH /me": () => problem(422, "1 invalid field", { errors: [{ loc: ["body", "display_name"], msg: "Too long" }] }),
    })
    renderSettings(<ProfileSettings />, { me: ME })
    fireEvent.change(within(nameForm()).getByRole("textbox", { name: "Name" }), { target: { value: "Priya" } })
    fireEvent.click(within(nameForm()).getByRole("button", { name: "Save name" }))

    expect(await within(nameForm()).findByText("Too long")).toBeTruthy()
  })
})

describe("Settings → Profile: password", () => {
  it("says that changing it signs out every other browser", () => {
    mockRoutes({})
    renderSettings(<ProfileSettings />, { me: ME })
    expect(
      screen.getByText("Changing it signs you out on every other browser and device. You stay signed in here."),
    ).toBeTruthy()
  })

  it("checks the new password before sending: the policy, and the same twice", async () => {
    const calls = mockRoutes({})
    renderSettings(<ProfileSettings />, { me: ME })

    fillPasswords(passwordForm(), "", "short")
    fireEvent.click(within(passwordForm()).getByRole("button", { name: "Change password" }))
    expect(await within(passwordForm()).findByText("Enter your current password.")).toBeTruthy()
    expect(within(passwordForm()).getByText("Use at least 12 characters")).toBeTruthy()

    fillPasswords(passwordForm(), "the old one", "a long enough passphrase", "a different passphrase")
    fireEvent.click(within(passwordForm()).getByRole("button", { name: "Change password" }))
    expect(await within(passwordForm()).findByText("The passwords don't match.")).toBeTruthy()
    expect(writesOf(calls)).toEqual([])
  })

  it("changes it, says the other sessions ended, and fetches the signed-in user again (new CSRF token)", async () => {
    const calls = mockRoutes({ "POST /me/password": () => noContent(), "GET /me": () => json(ME) })
    const { queryClient } = renderSettings(<ProfileSettings />, { me: ME })

    fillPasswords(passwordForm(), "the old one", "a long enough passphrase")
    fireEvent.click(within(passwordForm()).getByRole("button", { name: "Change password" }))

    const notice = await within(passwordForm()).findByRole("status")
    expect(notice.textContent).toContain("Your password has been changed")
    expect(notice.textContent).toContain("Every other browser and device is signed out.")
    expect(writesOf(calls)[0].body).toEqual({ current_password: "the old one", new_password: "a long enough passphrase" })
    expect(within(passwordForm()).getByLabelText("Current password")).toHaveProperty("value", "")
    expect(queryClient.getQueryState(queryKeys.me.all)?.isInvalidated).toBe(true)
  })

  it("puts a wrong current password (422) under its field", async () => {
    mockRoutes({
      "POST /me/password": () =>
        problem(422, "1 invalid field", {
          errors: [{ loc: ["body", "current_password"], msg: "Your current password is not correct" }],
        }),
    })
    renderSettings(<ProfileSettings />, { me: ME })
    fillPasswords(passwordForm(), "wrong", "a long enough passphrase")
    fireEvent.click(within(passwordForm()).getByRole("button", { name: "Change password" }))

    expect(await within(passwordForm()).findByText("Your current password is not correct")).toBeTruthy()
    expect(within(passwordForm()).getByLabelText("Current password").getAttribute("aria-invalid")).toBe("true")
  })

  it("says how long to wait after too many attempts (429)", async () => {
    mockRoutes({
      "POST /me/password": () => problem(429, "Too many attempts. Try again later.", { headers: { "Retry-After": "840" } }),
    })
    renderSettings(<ProfileSettings />, { me: ME })
    fillPasswords(passwordForm(), "wrong", "a long enough passphrase")
    fireEvent.click(within(passwordForm()).getByRole("button", { name: "Change password" }))

    const alert = await within(passwordForm()).findByRole("alert")
    expect(alert.textContent).toContain("Too many attempts")
    expect(alert.textContent).toContain("Try again in 14 minutes.")
  })
})
