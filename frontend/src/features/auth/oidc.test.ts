import { describe, expect, it } from "vitest"

import type { Schema } from "@/lib/api/client"

import { oidcErrorNotice, providerName } from "./oidc"

type Code = Schema<"OidcLoginError">

/** Every code the callback can send (the type check fails if the API adds one this table lacks). */
const EXPECTED = {
  no_account: ["error", "There is no HireFlow AI account for this address", "Ask your agency to invite you."],
  no_access: [
    "error",
    "Your account has no active agency access",
    "Ask an owner or admin of your agency to give you access again.",
  ],
  email_not_verified: [
    "error",
    "Google hasn't confirmed this email address",
    "Sign in with your email and password instead, or ask your agency to invite you.",
  ],
  invitation_invalid: [
    "error",
    "This invitation can't be used any more",
    "It has expired, or was replaced or already used. Ask for a new invitation.",
  ],
  invitation_mismatch: [
    "error",
    "This Google account belongs to another address",
    "The invitation is for a different email address. Use the account for that address, or ask for an invitation to yours.",
  ],
  account_exists: [
    "error",
    "You already have an account for this address",
    "Sign in with your email and password, then open the invitation link again to join.",
  ],
  already_member: ["info", "You're already a member of this agency", "Sign in to open it, with Google or your password."],
  cancelled: ["info", "Sign-in with Google was cancelled", "Try again, or sign in with your email and password."],
  expired: [
    "error",
    "Sign-in with Google took too long",
    "Start again from this page, and finish within 10 minutes in the same browser.",
  ],
  oidc_failed: [
    "error",
    "Sign-in with Google didn't work",
    "Try again. If it keeps failing, sign in with your email and password.",
  ],
  rate_limited: ["error", "Too many sign-in attempts from this network", "Try again in a few minutes."],
} satisfies Record<Code, [string, string, string]>

describe("oidcErrorNotice", () => {
  it.each(Object.entries(EXPECTED))("words %s", (code, [tone, title, description]) => {
    expect(oidcErrorNotice(code, "google")).toEqual({ tone, title, description })
  })

  it("does without the provider's name when the URL has none or an unknown one", () => {
    expect(oidcErrorNotice("cancelled", undefined).title).toBe("Signing in was cancelled")
    expect(oidcErrorNotice("email_not_verified", "myspace").title).toBe("The provider hasn't confirmed this email address")
    expect(oidcErrorNotice("already_member", null).description).toBe("Sign in to open it.")
  })

  it("has a general message for a code it does not know", () => {
    expect(oidcErrorNotice("something_new", "microsoft")).toEqual({
      tone: "error",
      title: "Signing in didn't work",
      description: "Try again, or sign in with your email and password.",
    })
    expect(oidcErrorNotice("toString", "google").title).toBe("Signing in didn't work")
  })
})

describe("providerName", () => {
  it("names the two providers and nothing else", () => {
    expect(providerName("google")).toBe("Google")
    expect(providerName("microsoft")).toBe("Microsoft")
    expect(providerName("constructor")).toBeNull()
    expect(providerName(undefined)).toBeNull()
  })
})
