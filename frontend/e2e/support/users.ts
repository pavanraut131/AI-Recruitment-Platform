import path from "node:path"

import { expect, request, type APIRequestContext, type Page } from "@playwright/test"

/*
 * Signing in during the E2E run (EPIC-02 §11.4). The E2E stack's seed creates
 * one account per role in two agencies (`app.devtools.seed_users`), all with
 * the development-only password; the README lists them.
 */

export const DEV_PASSWORD = "hireflow-dev-password"

export const USERS = {
  /** The hero flow's user (spec §11.4: it runs as a recruiter), signed in once by `auth.setup.ts`. */
  recruiter: "recruiter@default.example",
  defaultAdmin: "admin@default.example",
  /** Invites into the default agency (team.spec). */
  defaultOwner: "owner@default.example",
  /** The read-only role's checks (team.spec). */
  defaultViewer: "viewer@default.example",
  northbankOwner: "owner@northbank.example",
  northbankAdmin: "admin@northbank.example",
  northbankRecruiter: "recruiter@northbank.example",
  /** Its password is changed by the reset test; nothing else signs in with it. */
  northbankViewer: "viewer@northbank.example",
} as const

/** The recruiter's signed-in browser state, shared by the specs (gitignored, never uploaded by CI). */
export const RECRUITER_STATE = path.join(__dirname, "..", "..", "e2e-results", ".auth", "recruiter.json")

/** The API's origin, for the few steps a test does without the UI (inviting someone). */
export const API_URL = process.env.E2E_API_URL ?? "http://localhost:18000"

/** Signs in through the real login page (the page must be on `/login`). */
export async function signIn(page: Page, email: string, password: string = DEV_PASSWORD): Promise<void> {
  const form = page.getByRole("form", { name: "Sign in" })
  await form.getByLabel("Email").fill(email)
  await form.getByLabel("Password", { exact: true }).fill(password)
  await form.getByRole("button", { name: "Sign in" }).click()
}

/** An API client signed in as `email`, with the session's CSRF token for unsafe requests. */
export async function apiSession(email: string): Promise<{ api: APIRequestContext; csrfToken: string }> {
  const api = await request.newContext({ baseURL: `${API_URL}/api/v2/` })
  const csrf = await api.get("auth/csrf")
  expect(csrf.ok()).toBe(true)
  const { csrf_token: anonymous } = (await csrf.json()) as { csrf_token: string }
  const login = await api.post("auth/login", {
    data: { email, password: DEV_PASSWORD },
    headers: { "X-CSRF-Token": anonymous },
  })
  expect(login.ok(), `sign in as ${email}: ${login.status()}`).toBe(true)
  // Sign-in starts a new session with a new CSRF token (spec §4.3).
  const { csrf_token: csrfToken } = (await login.json()) as { csrf_token: string }
  return { api, csrfToken }
}
