import { fireEvent, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { queryKeys } from "@/lib/api/query-keys"
import { makeQueryClient } from "@/lib/query/query-client"
import { installApi, invitationFixture, json, meFixture, noContent, problem } from "@/test/auth"
import { renderWithProviders } from "@/test/helpers"

import { AcceptInvitation } from "./accept-invite"
import { forgetFragmentTokens } from "./use-fragment-token"

const { replace } = vi.hoisted(() => ({ replace: vi.fn() }))
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const TOKEN = "invite-token-0123456789abcdef"
const GOOD = "a long enough passphrase"
const SIGNED_OUT = { "GET /me": () => problem(401, "Not signed in") }

function renderPage() {
  const queryClient = makeQueryClient()
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } })
  renderWithProviders(<AcceptInvitation />, { queryClient })
  return queryClient
}

beforeEach(() => {
  replace.mockClear()
  forgetFragmentTokens()
  window.history.replaceState(null, "", `/accept-invite#token=${TOKEN}`)
})

describe("AcceptInvitation", () => {
  it("previews the invitation: agency, who sent it, role and address; the token goes in the body only", async () => {
    const calls = installApi({ ...SIGNED_OUT, "POST /auth/invitations/preview": () => json(invitationFixture()) })
    renderPage()

    expect(await screen.findByRole("heading", { name: "Join Northbank Talent" })).toBeTruthy()
    expect(document.body.textContent).toContain("Olu Owner invited you to join Northbank Talent as a recruiter.")
    expect(((await screen.findByLabelText("Email")) as HTMLInputElement).value).toBe("new.person@northbank.example")
    const preview = calls.find((call) => call.path === "/auth/invitations/preview")!
    expect(preview.body).toEqual({ token: TOKEN })
    expect(calls.every((call) => !call.url.href.includes(TOKEN))).toBe(true)
    expect(window.location.hash).toBe("")
  })

  it("creates the account with a name and a password, then opens Home in the new session", async () => {
    const me = meFixture({ user: { id: "u-2", email: "new.person@northbank.example", display_name: "Nia Person" } })
    const calls = installApi({
      ...SIGNED_OUT,
      "POST /auth/invitations/preview": () => json(invitationFixture()),
      "POST /auth/invitations/accept": () => json(me),
    })
    const queryClient = renderPage()
    await screen.findByRole("heading", { name: "Join Northbank Talent" })

    fireEvent.change(await screen.findByLabelText("Your name"), { target: { value: "Nia Person" } })
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: GOOD } })
    fireEvent.click(screen.getByRole("button", { name: "Join Northbank Talent" }))

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"))
    const accept = calls.find((call) => call.path === "/auth/invitations/accept")!
    expect(accept.body).toEqual({ token: TOKEN, display_name: "Nia Person", password: GOOD })
    expect(queryClient.getQueryData(queryKeys.me.all)).toEqual(me)
  })

  it("checks the new account's name and password policy before sending", async () => {
    const calls = installApi({ ...SIGNED_OUT, "POST /auth/invitations/preview": () => json(invitationFixture()) })
    renderPage()
    await screen.findByRole("heading", { name: "Join Northbank Talent" })

    fireEvent.change(await screen.findByLabelText("Password"), { target: { value: "short" } })
    fireEvent.click(screen.getByRole("button", { name: "Join Northbank Talent" }))

    expect(await screen.findByText("Enter your name")).toBeTruthy()
    expect(screen.getByText("Use at least 12 characters")).toBeTruthy()
    expect(calls.some((call) => call.path === "/auth/invitations/accept")).toBe(false)
  })

  it("explains an expired, used or replaced invitation (410)", async () => {
    installApi({
      ...SIGNED_OUT,
      "POST /auth/invitations/preview": () =>
        problem(410, "This invitation has expired or is no longer valid. Ask for a new invitation."),
    })
    renderPage()

    expect(await screen.findByRole("heading", { name: "This invitation can't be used any more" })).toBeTruthy()
    expect(document.body.textContent).toContain("Ask for a new invitation.")
  })

  it("offers Google and Microsoft with the invitation's token, and explains a 410 from them", async () => {
    const calls = installApi({
      ...SIGNED_OUT,
      "POST /auth/invitations/preview": () => json(invitationFixture()),
      "GET /auth/providers": () => json({ providers: [{ id: "google", name: "Google" }] }),
      "POST /auth/oidc/google/start": () =>
        problem(410, "This invitation has expired or is no longer valid. Ask for a new invitation."),
    })
    renderPage()

    fireEvent.click(await screen.findByRole("button", { name: "Continue with Google" }))

    expect(await screen.findByRole("heading", { name: "This invitation can't be used any more" })).toBeTruthy()
    expect(calls.find((call) => call.path === "/auth/oidc/google/start")!.body).toEqual({
      next: "/",
      invitation_token: TOKEN,
    })
  })

  it("explains a link without a token, without asking the API", async () => {
    window.history.replaceState(null, "", "/accept-invite")
    const calls = installApi(SIGNED_OUT)
    renderPage()

    expect(await screen.findByRole("heading", { name: "This invitation link is incomplete" })).toBeTruthy()
    expect(calls.some((call) => call.path.startsWith("/auth/invitations"))).toBe(false)
  })

  it("has an existing account sign in first, then join with it", async () => {
    const me = meFixture({ user: { id: "u-3", email: "new.person@northbank.example", display_name: "Nia Person" } })
    const calls = installApi({
      ...SIGNED_OUT,
      "POST /auth/invitations/preview": () => json(invitationFixture({ account_exists: true })),
      "POST /auth/login": () => json(me),
      "POST /auth/invitations/accept": () => json(me),
    })
    renderPage()
    expect(await screen.findByText(/You already have a HireFlow AI account/)).toBeTruthy()

    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "my old password" } })
    fireEvent.click(screen.getByRole("button", { name: "Sign in and join Northbank Talent" }))

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"))
    const paths = calls.filter((call) => call.method === "POST").map((call) => call.path)
    expect(paths).toEqual(["/auth/invitations/preview", "/auth/login", "/auth/invitations/accept"])
    expect(calls.find((call) => call.path === "/auth/login")!.body).toEqual({
      email: "new.person@northbank.example",
      password: "my old password",
    })
    expect(calls.find((call) => call.path === "/auth/invitations/accept")!.body).toEqual({ token: TOKEN })
  })

  it("switches a new-account form to signing in when the address has an account after all (409)", async () => {
    installApi({
      ...SIGNED_OUT,
      "POST /auth/invitations/preview": () => json(invitationFixture()),
      "POST /auth/invitations/accept": () => problem(409, "An account with this email exists. Sign in, then accept."),
    })
    renderPage()
    await screen.findByRole("heading", { name: "Join Northbank Talent" })

    fireEvent.change(await screen.findByLabelText("Your name"), { target: { value: "Nia Person" } })
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: GOOD } })
    fireEvent.click(screen.getByRole("button", { name: "Join Northbank Talent" }))

    expect(await screen.findByRole("button", { name: "Sign in and join Northbank Talent" })).toBeTruthy()
  })

  it("joins at once when already signed in as the invited address", async () => {
    const me = meFixture({ user: { id: "u-3", email: "New.Person@northbank.example", display_name: "Nia Person" } })
    const calls = installApi({
      "GET /me": () => json(me),
      "POST /auth/invitations/preview": () => json(invitationFixture({ account_exists: true })),
      "POST /auth/invitations/accept": () => json(me),
    })
    renderPage()

    fireEvent.click(await screen.findByRole("button", { name: "Join Northbank Talent" }))

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"))
    expect(calls.find((call) => call.path === "/auth/invitations/accept")!.body).toEqual({ token: TOKEN })
  })

  it("offers to sign out when signed in as another address", async () => {
    let signedIn = true
    const calls = installApi({
      "GET /me": () => (signedIn ? json(meFixture()) : problem(401, "Not signed in")),
      "POST /auth/invitations/preview": () => json(invitationFixture({ account_exists: true })),
      "POST /auth/logout": () => {
        signedIn = false
        return noContent()
      },
    })
    renderPage()

    expect((await screen.findByRole("status")).textContent).toContain(
      "You're signed in as priya@northbank.example, but this invitation is for new.person@northbank.example.",
    )
    fireEvent.click(screen.getByRole("button", { name: "Sign out and continue" }))

    expect(await screen.findByRole("button", { name: "Sign in and join Northbank Talent" })).toBeTruthy()
    expect(calls.some((call) => call.path === "/auth/logout")).toBe(true)
  })
})
