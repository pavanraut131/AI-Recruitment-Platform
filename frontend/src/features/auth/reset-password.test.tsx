import { act, fireEvent, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { CSRF_HEADER } from "@/lib/api/csrf"
import { installApi, noContent, problem } from "@/test/auth"
import { renderWithProviders } from "@/test/helpers"

import { ResetPassword } from "./reset-password"
import { forgetFragmentTokens, tokenFromHash } from "./use-fragment-token"

const { navigateTo } = vi.hoisted(() => ({ navigateTo: vi.fn() }))
vi.mock("@/lib/auth/navigate", () => ({ navigateTo }))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const TOKEN = "reset-token-0123456789abcdef"
const GOOD = "a long enough passphrase"

function openLink(hash: string) {
  window.history.replaceState(null, "", `/reset-password${hash}`)
}

function choose(password: string, again = password) {
  fireEvent.change(screen.getByLabelText("New password"), { target: { value: password } })
  fireEvent.change(screen.getByLabelText("New password, again"), { target: { value: again } })
  fireEvent.click(screen.getByRole("button", { name: "Change password" }))
}

beforeEach(() => {
  navigateTo.mockClear()
  forgetFragmentTokens()
})

describe("tokenFromHash", () => {
  it("reads #token=… and nothing else", () => {
    expect(tokenFromHash("#token=abc")).toBe("abc")
    expect(tokenFromHash("#other=1&token=abc")).toBe("abc")
    expect(tokenFromHash("#token=")).toBeNull()
    expect(tokenFromHash("")).toBeNull()
  })
})

describe("ResetPassword", () => {
  it("reads the token from the fragment and takes it out of the address bar", async () => {
    openLink(`#token=${TOKEN}`)
    installApi({})
    renderWithProviders(<ResetPassword />)

    expect(await screen.findByRole("heading", { name: "Choose a new password" })).toBeTruthy()
    expect(window.location.hash).toBe("")
    expect(window.location.pathname).toBe("/reset-password")
  })

  it("sends the token in the body only (never in a URL), then opens the login page with a confirmation", async () => {
    openLink(`#token=${TOKEN}`)
    const calls = installApi({ "POST /auth/password-reset/confirm": () => noContent() })
    renderWithProviders(<ResetPassword />)
    await screen.findByRole("heading", { name: "Choose a new password" })

    choose(GOOD)

    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith("/login?reason=password_changed"))
    expect(calls).toHaveLength(1)
    expect(calls[0].body).toEqual({ token: TOKEN, new_password: GOOD })
    expect(calls[0].url.href).not.toContain(TOKEN)
    expect(calls[0].headers.get(CSRF_HEADER)).toBeTruthy()
  })

  it("checks the password policy and that both entries match before sending", async () => {
    openLink(`#token=${TOKEN}`)
    const calls = installApi({})
    renderWithProviders(<ResetPassword />)
    await screen.findByRole("heading", { name: "Choose a new password" })

    choose("too short")
    expect(await screen.findByText("Use at least 12 characters")).toBeTruthy()

    choose(GOOD, `${GOOD}!`)
    expect(await screen.findByText("The two passwords are different")).toBeTruthy()
    expect(calls).toHaveLength(0)
  })

  it("puts the API's policy refusal (422) under the field; the link still works", async () => {
    openLink(`#token=${TOKEN}`)
    installApi({
      "POST /auth/password-reset/confirm": () =>
        problem(422, "1 invalid field", {
          errors: [{ loc: ["body", "new_password"], msg: "Don't use your email address as your password" }],
        }),
    })
    renderWithProviders(<ResetPassword />)
    await screen.findByRole("heading", { name: "Choose a new password" })

    choose("priya@northbank.example")

    expect(await screen.findByText("Don't use your email address as your password")).toBeTruthy()
    expect(navigateTo).not.toHaveBeenCalled()
  })

  it("explains an expired or used link (410) and offers a new one", async () => {
    openLink(`#token=${TOKEN}`)
    installApi({
      "POST /auth/password-reset/confirm": () =>
        problem(410, "This link has expired or was already used. Ask for a new one."),
    })
    renderWithProviders(<ResetPassword />)
    await screen.findByRole("heading", { name: "Choose a new password" })

    choose(GOOD)

    expect(await screen.findByRole("heading", { name: "This link can't be used any more" })).toBeTruthy()
    expect(screen.getByRole("link", { name: "Ask for a new link" }).getAttribute("href")).toBe("/forgot-password")
  })

  it("explains a link without a token", async () => {
    openLink("")
    installApi({})
    renderWithProviders(<ResetPassword />)

    expect(await screen.findByRole("heading", { name: "This link is incomplete" })).toBeTruthy()
  })

  it("follows another link opened in the same tab", async () => {
    openLink(`#token=${TOKEN}`)
    const calls = installApi({ "POST /auth/password-reset/confirm": () => noContent() })
    renderWithProviders(<ResetPassword />)
    await screen.findByRole("heading", { name: "Choose a new password" })

    act(() => {
      openLink("#token=a-newer-token")
      window.dispatchEvent(new HashChangeEvent("hashchange"))
    })
    choose(GOOD)

    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0].body).toEqual({ token: "a-newer-token", new_password: GOOD })
  })
})
