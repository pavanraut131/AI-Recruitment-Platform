import { fireEvent, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { installApi, json, problem } from "@/test/auth"
import { renderWithProviders } from "@/test/helpers"

import { ForgotPassword } from "./forgot-password"

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const SENT = { detail: "If an account exists for this address, we've sent it a link to choose a new password." }

function submit(email: string) {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } })
  fireEvent.click(screen.getByRole("button", { name: "Email me a link" }))
}

describe("ForgotPassword", () => {
  it("asks for an address first", async () => {
    const calls = installApi({})
    renderWithProviders(<ForgotPassword />)

    fireEvent.click(screen.getByRole("button", { name: "Email me a link" }))

    expect(await screen.findByText("Enter your email address")).toBeTruthy()
    expect(calls).toHaveLength(0)
  })

  it("always shows the same confirmation, whatever the address (no account enumeration)", async () => {
    const calls = installApi({ "POST /auth/password-reset": () => json(SENT, 202) })
    renderWithProviders(<ForgotPassword />)

    submit("nobody@example.com")

    expect(await screen.findByRole("heading", { name: "Check your email" })).toBeTruthy()
    expect(document.body.textContent).toContain("If an account exists for nobody@example.com")
    expect(calls[0].body).toEqual({ email: "nobody@example.com" })
  })

  it("lets someone send another link to a corrected address", async () => {
    installApi({ "POST /auth/password-reset": () => json(SENT, 202) })
    renderWithProviders(<ForgotPassword />)
    submit("typo@example.con")
    await screen.findByRole("heading", { name: "Check your email" })

    fireEvent.click(screen.getByRole("button", { name: "send another link" }))

    expect(screen.getByRole("heading", { name: "Reset your password" })).toBeTruthy()
    expect((screen.getByLabelText("Email") as HTMLInputElement).value).toBe("typo@example.con")
  })

  it("says how long to wait after too many requests (429)", async () => {
    installApi({
      "POST /auth/password-reset": () => problem(429, "Too many attempts. Try again later.", {}, { "Retry-After": "3000" }),
    })
    renderWithProviders(<ForgotPassword />)

    submit("priya@northbank.example")

    expect((await screen.findByRole("alert")).textContent).toContain("Try again in 50 minutes.")
  })
})
