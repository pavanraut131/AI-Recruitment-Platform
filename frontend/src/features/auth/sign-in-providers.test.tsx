import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { CSRF_HEADER } from "@/lib/api/csrf"
import { makeQueryClient } from "@/lib/query/query-client"
import { clearCsrfCookie, installApi, json, problem } from "@/test/auth"
import { renderWithProviders } from "@/test/helpers"

import { SignInProviders, type SignInProvidersProps } from "./sign-in-providers"

const { navigateTo } = vi.hoisted(() => ({ navigateTo: vi.fn() }))
vi.mock("@/lib/auth/navigate", () => ({ navigateTo }))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const BOTH = {
  providers: [
    { id: "google", name: "Google" },
    { id: "microsoft", name: "Microsoft" },
  ],
}
const AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth?client_id=x&state=s"

function renderButtons(props: Partial<SignInProvidersProps> = {}) {
  const queryClient = makeQueryClient()
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } })
  renderWithProviders(
    <div data-testid="slot">
      <SignInProviders next="/campaigns" {...props} />
    </div>,
    { queryClient },
  )
}

function slot() {
  return screen.getByTestId("slot")
}

beforeEach(() => {
  navigateTo.mockClear()
})

describe("SignInProviders", () => {
  it("shows one button per provider the API offers, in its order, above an 'or'", async () => {
    installApi({ "GET /auth/providers": () => json(BOTH) })
    renderButtons()

    const group = await screen.findByRole("group", { name: "Sign in with another account" })
    expect(within(group).getAllByRole("button").map((button) => button.textContent)).toEqual([
      "Continue with Google",
      "Continue with Microsoft",
    ])
    expect(slot().textContent).toContain("or")
  })

  it("shows nothing at all when no provider is configured", async () => {
    const calls = installApi({ "GET /auth/providers": () => json({ providers: [] }) })
    renderButtons()

    await waitFor(() => expect(calls.some((call) => call.path === "/auth/providers")).toBe(true))
    await waitFor(() => expect(slot().textContent).toBe(""))
    expect(screen.queryByRole("button")).toBeNull()
  })

  it("shows nothing when the list cannot be had (the email form still works)", async () => {
    const calls = installApi({ "GET /auth/providers": () => problem(500) })
    renderButtons()

    await waitFor(() => expect(calls).toHaveLength(1))
    expect(slot().textContent).toBe("")
  })

  it("starts the flow with next and the CSRF header, then opens the provider's page", async () => {
    const calls = installApi({
      "GET /auth/providers": () => json(BOTH),
      "POST /auth/oidc/google/start": () => json({ authorization_url: AUTHORIZE }),
    })
    renderButtons()

    fireEvent.click(await screen.findByRole("button", { name: "Continue with Google" }))

    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith(AUTHORIZE))
    const started = calls.find((call) => call.path === "/auth/oidc/google/start")!
    expect(started.body).toEqual({ next: "/campaigns", invitation_token: null })
    expect(started.headers.get(CSRF_HEADER)).toBe("vitest-csrf-token")
    expect(started.credentials).toBe("include")
    expect(screen.getByRole("button", { name: "Opening Google…" })).toBeTruthy()
  })

  it("asks for an anonymous CSRF token first when the browser has none", async () => {
    clearCsrfCookie()
    const calls = installApi({
      "GET /auth/providers": () => json(BOTH),
      "GET /auth/csrf": () => json({ csrf_token: "anonymous-token" }),
      "POST /auth/oidc/microsoft/start": () => json({ authorization_url: AUTHORIZE }),
    })
    renderButtons()

    fireEvent.click(await screen.findByRole("button", { name: "Continue with Microsoft" }))

    await waitFor(() => expect(navigateTo).toHaveBeenCalled())
    expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual([
      "GET /auth/providers",
      "GET /auth/csrf",
      "POST /auth/oidc/microsoft/start",
    ])
    expect(calls[2].headers.get(CSRF_HEADER)).toBe("anonymous-token")
  })

  it("sends the invitation's token when accepting an invitation", async () => {
    const calls = installApi({
      "GET /auth/providers": () => json(BOTH),
      "POST /auth/oidc/microsoft/start": () => json({ authorization_url: AUTHORIZE }),
    })
    renderButtons({ next: "/", invitationToken: "invite-token" })

    fireEvent.click(await screen.findByRole("button", { name: "Continue with Microsoft" }))

    await waitFor(() => expect(navigateTo).toHaveBeenCalled())
    expect(calls.find((call) => call.method === "POST")!.body).toEqual({ next: "/", invitation_token: "invite-token" })
  })

  it("hands a 410 to the invitation page", async () => {
    installApi({
      "GET /auth/providers": () => json(BOTH),
      "POST /auth/oidc/google/start": () =>
        problem(410, "This invitation has expired or is no longer valid. Ask for a new invitation."),
    })
    const onInvitationGone = vi.fn()
    renderButtons({ invitationToken: "invite-token", onInvitationGone })

    fireEvent.click(await screen.findByRole("button", { name: "Continue with Google" }))

    await waitFor(() =>
      expect(onInvitationGone).toHaveBeenCalledWith(
        "This invitation has expired or is no longer valid. Ask for a new invitation.",
      ),
    )
    expect(navigateTo).not.toHaveBeenCalled()
  })

  it.each([
    [429, { "Retry-After": "90" }, "Too many attempts", "Try again in 2 minutes."],
    [502, {}, "Google can't be reached right now", "Try again in a moment, or sign in with your email and password."],
  ])("explains a %s when starting", async (status, headers, title, description) => {
    installApi({
      "GET /auth/providers": () => json(BOTH),
      "POST /auth/oidc/google/start": () => problem(status, "detail from the API", {}, headers),
    })
    renderButtons()

    fireEvent.click(await screen.findByRole("button", { name: "Continue with Google" }))

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain(title)
    expect(alert.textContent).toContain(description)
    expect(navigateTo).not.toHaveBeenCalled()
    expect((screen.getByRole("button", { name: "Continue with Google" }) as HTMLButtonElement).disabled).toBe(false)
  })

  it("drops a provider that is no longer configured (404), and says so", async () => {
    let configured = BOTH
    installApi({
      "GET /auth/providers": () => json(configured),
      "POST /auth/oidc/microsoft/start": () => {
        configured = { providers: [BOTH.providers[0]] }
        return problem(404, "Not Found")
      },
    })
    renderButtons()

    fireEvent.click(await screen.findByRole("button", { name: "Continue with Microsoft" }))

    expect((await screen.findByRole("alert")).textContent).toContain("Sign-in with Microsoft isn't available any more")
    await waitFor(() => expect(screen.queryByRole("button", { name: "Continue with Microsoft" })).toBeNull())
    expect(screen.getByRole("button", { name: "Continue with Google" })).toBeTruthy()
  })
})
