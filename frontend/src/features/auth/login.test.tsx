import { fireEvent, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { CSRF_HEADER } from "@/lib/api/csrf"
import { queryKeys } from "@/lib/api/query-keys"
import { makeQueryClient } from "@/lib/query/query-client"
import { installApi, json, meFixture, problem } from "@/test/auth"
import { renderWithProviders } from "@/test/helpers"

import { Login } from "./login"

const { replace } = vi.hoisted(() => ({ replace: vi.fn() }))
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const SIGNED_OUT = { "GET /me": () => problem(401, "Not signed in"), "GET /auth/csrf": () => json({ csrf_token: "fresh" }) }

function renderLogin(props: Partial<Parameters<typeof Login>[0]> = {}) {
  const queryClient = makeQueryClient()
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } })
  queryClient.setQueryData(queryKeys.home.all, { from: "an earlier session" })
  renderWithProviders(<Login next="/campaigns" {...props} />, { queryClient })
  return queryClient
}

function fill(email: string, password: string) {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } })
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } })
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }))
}

beforeEach(() => {
  replace.mockClear()
})

describe("Login", () => {
  it("asks for both fields before sending anything", async () => {
    const calls = installApi(SIGNED_OUT)
    renderLogin()

    fireEvent.click(screen.getByRole("button", { name: "Sign in" }))

    expect(await screen.findByText("Enter your email address")).toBeTruthy()
    expect(screen.getByText("Enter your password")).toBeTruthy()
    expect(calls.some((call) => call.path === "/auth/login")).toBe(false)
  })

  it("fetches a CSRF token for this browser when it opens", async () => {
    const calls = installApi(SIGNED_OUT)
    renderLogin()

    await waitFor(() => expect(calls.some((call) => call.path === "/auth/csrf")).toBe(true))
  })

  it("signs in with the CSRF header, starts a fresh cache and goes on to next", async () => {
    const me = meFixture()
    const calls = installApi({ ...SIGNED_OUT, "POST /auth/login": () => json(me) })
    const queryClient = renderLogin()

    fill(" priya@northbank.example ", "correct horse battery")

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/campaigns"))
    const login = calls.find((call) => call.path === "/auth/login")!
    expect(login.body).toEqual({ email: "priya@northbank.example", password: "correct horse battery" })
    expect(login.headers.get(CSRF_HEADER)).toBeTruthy()
    expect(login.credentials).toBe("include")
    expect(queryClient.getQueryData(queryKeys.home.all)).toBeUndefined()
    expect(queryClient.getQueryData(queryKeys.me.all)).toEqual(me)
  })

  it("sends a user without an active agency to the agency picker, then next", async () => {
    installApi({
      ...SIGNED_OUT,
      "POST /auth/login": () => json(meFixture({ active_agency: null, role: null, permissions: [], client_scope: null })),
    })
    renderLogin({ next: "/talent" })

    fill("priya@northbank.example", "correct horse battery")

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/select-agency?next=%2Ftalent"))
  })

  it("shows the API's one message for a wrong email or password, and clears the password", async () => {
    installApi({ ...SIGNED_OUT, "POST /auth/login": () => problem(401, "Invalid email or password") })
    renderLogin()

    fill("priya@northbank.example", "wrong password")

    expect((await screen.findByRole("alert")).textContent).toContain("Invalid email or password")
    expect((screen.getByLabelText("Password") as HTMLInputElement).value).toBe("")
    expect(replace).not.toHaveBeenCalled()
  })

  it("says how long to wait after too many attempts (429, Retry-After)", async () => {
    installApi({
      ...SIGNED_OUT,
      "POST /auth/login": () => problem(429, "Too many attempts. Try again later.", {}, { "Retry-After": "840" }),
    })
    renderLogin()

    fill("priya@northbank.example", "whatever it is")

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("Too many attempts")
    expect(alert.textContent).toContain("Try again in 14 minutes.")
  })

  it("puts a 422 under its field", async () => {
    installApi({
      ...SIGNED_OUT,
      "POST /auth/login": () =>
        problem(422, "1 invalid field", { errors: [{ loc: ["body", "email"], msg: "String should have at most 320 characters" }] }),
    })
    renderLogin()

    fill("priya@northbank.example", "whatever it is")

    expect(await screen.findByText("String should have at most 320 characters")).toBeTruthy()
    expect(screen.queryByRole("alert")).toBeNull()
  })

  it("explains an account with no agency access (403)", async () => {
    installApi({ ...SIGNED_OUT, "POST /auth/login": () => problem(403, "Your account has no active agency access") })
    renderLogin()

    fill("priya@northbank.example", "correct horse battery")

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("Your account has no active agency access")
    expect(alert.textContent).toContain("Ask an owner or admin")
  })

  it("says a session ended, a password changed, or that there is no account (OIDC)", () => {
    installApi(SIGNED_OUT)
    renderLogin({ reason: "expired" })
    expect(screen.getByRole("status").textContent).toContain("Your session ended")
  })

  it.each([
    ["password_changed", undefined, "Your password has been changed"],
    [undefined, "no_account", "There is no HireFlow AI account for this address"],
  ])("shows reason=%s error=%s", (reason, error, text) => {
    installApi(SIGNED_OUT)
    renderLogin({ reason, error })
    expect(document.body.textContent).toContain(text)
  })

  it("sends someone already signed in straight on to next", async () => {
    installApi({ "GET /me": () => json(meFixture()), "GET /auth/csrf": () => json({ csrf_token: "t" }) })
    renderLogin({ next: "/talent?q=ada" })

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/talent?q=ada"))
  })
})
