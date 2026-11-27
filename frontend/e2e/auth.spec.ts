import { expect, test, type Page } from "@playwright/test"

import { checkScreen } from "./support/checks"
import { newRunId } from "./support/hero-data"
import { latestEmailId, waitForLink } from "./support/mailpit"
import { apiSession, signIn, USERS } from "./support/users"

/*
 * Signing in and out, and the emailed-link pages (EPIC-02 §9.1, E2-09), with
 * axe and screenshots of every sign-in page in both themes. Each test starts
 * signed out (no saved state) and uses its own seeded account, so none ends
 * another's session. The full auth, invitation, roles and isolation suite is
 * E2-11's.
 */
test.use({ storageState: { cookies: [], origins: [] } })

function userMenu(page: Page, name: RegExp) {
  return page.getByRole("button", { name })
}

test("sign in, Home, sign out", async ({ page }, testInfo) => {
  const providers = page.waitForResponse((answer) => answer.url().endsWith("/api/v2/auth/providers"))
  // Signed out: the server redirects before the app renders (proxy.ts).
  const response = await page.goto("/campaigns")
  expect(response?.request().redirectedFrom()?.url()).toMatch(/\/campaigns$/)
  await expect(page).toHaveURL(/\/login\?next=%2Fcampaigns$/)
  await expect(page.getByRole("heading", { level: 1, name: "Sign in to HireFlow AI" })).toBeVisible()
  // No Google or Microsoft app is configured on this stack, so no button shows.
  expect(await (await providers).json()).toEqual({ providers: [] })
  await expect(page.getByRole("button", { name: /^Continue with/ })).toHaveCount(0)
  await checkScreen(page, testInfo, "auth-01-login")

  await page.goto("/login")
  await signIn(page, USERS.northbankRecruiter)
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible()
  await expect(page.getByText(/^Good (morning|afternoon|evening), Recruiter$/)).toBeVisible()

  await userMenu(page, /Recruiter \(Northbank Talent\)/).click()
  const menu = page.getByRole("menu")
  await expect(menu).toContainText(USERS.northbankRecruiter)
  await expect(menu).toContainText("Northbank Talent")
  await menu.getByRole("menuitem", { name: "Sign out" }).click()
  await expect(page).toHaveURL(/\/login$/)

  // The session is over: the app is out of reach again.
  await page.goto("/")
  await expect(page).toHaveURL(/\/login$/)
})

test("a wrong password says so, without saying which part was wrong", async ({ page }, testInfo) => {
  await page.goto("/login")
  await signIn(page, USERS.northbankAdmin, "not the password")

  // Scoped to the form: Next.js's route announcer is an alert too.
  await expect(page.getByRole("form", { name: "Sign in" }).getByRole("alert")).toContainText("Invalid email or password")
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByLabel("Password", { exact: true })).toHaveValue("")
  await checkScreen(page, testInfo, "auth-02-login-wrong-password")
})

test("a Google or Microsoft sign-in that found no account is explained", async ({ page }, testInfo) => {
  // Where the API's callback sends the browser (E2-08); no provider is needed to show the page.
  await page.goto("/login?error=no_account&provider=google")

  const notice = page.getByRole("main").getByRole("alert")
  await expect(notice).toContainText("There is no HireFlow AI account for this address")
  await expect(notice).toContainText("Ask your agency to invite you.")
  await checkScreen(page, testInfo, "auth-08-login-no-account")
})

test("next: a signed-out visit comes back to the same page after signing in", async ({ page }) => {
  await page.goto("/talent?q=ada")
  await expect(page).toHaveURL(/\/login\?next=%2Ftalent%3Fq%3Dada$/)

  await signIn(page, USERS.defaultAdmin)

  await expect(page).toHaveURL(/\/talent\?q=ada$/)
  await expect(page.getByRole("heading", { level: 1, name: "Talent" })).toBeVisible()
  // Signed in, the login page sends you straight on.
  await page.goto("/login?next=%2Fcampaigns")
  await expect(page).toHaveURL(/\/campaigns$/)
})

test("forgot password → the email → a new password → sign in with it", async ({ page }, testInfo) => {
  const address = USERS.northbankViewer
  const newPassword = `e2e ${newRunId()} new password`

  await page.goto("/login")
  await page.getByRole("link", { name: "Forgot password?" }).click()
  await expect(page.getByRole("heading", { level: 1, name: "Reset your password" })).toBeVisible()
  await checkScreen(page, testInfo, "auth-03-forgot-password")

  const before = await latestEmailId(address)
  await page.getByLabel("Email").fill(address)
  await page.getByRole("button", { name: "Email me a link" }).click()
  await expect(page.getByRole("heading", { level: 1, name: "Check your email" })).toBeVisible()
  await checkScreen(page, testInfo, "auth-04-forgot-password-sent")

  const link = await waitForLink(address, "/reset-password", before)
  await page.goto(link)
  await expect(page.getByRole("heading", { level: 1, name: "Choose a new password" })).toBeVisible()
  // The token is read and taken out of the address bar.
  await expect(page).toHaveURL(/\/reset-password$/)
  await checkScreen(page, testInfo, "auth-05-reset-password")

  await page.getByLabel("New password", { exact: true }).fill(newPassword)
  await page.getByLabel("New password, again", { exact: true }).fill(newPassword)
  await page.getByRole("button", { name: "Change password" }).click()

  await expect(page).toHaveURL(/\/login\?reason=password_changed$/)
  await expect(page.getByRole("main").getByRole("status")).toContainText("Your password has been changed")
  await signIn(page, address, newPassword)
  await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible()
})

test("an invitation: the preview, a new account, and Home in the agency", async ({ page }, testInfo) => {
  const address = `e2e-${newRunId()}@northbank.example`
  const { api, csrfToken } = await apiSession(USERS.northbankOwner)
  const invited = await api.post("team/invitations", {
    data: { email: address, role: "recruiter", all_clients: true, client_ids: [] },
    headers: { "X-CSRF-Token": csrfToken },
  })
  expect(invited.status(), await invited.text()).toBe(201)
  await api.dispose()

  const link = await waitForLink(address, "/accept-invite", null)
  await page.goto(link)
  await expect(page.getByRole("heading", { level: 1, name: "Join Northbank Talent" })).toBeVisible()
  await expect(page.getByText(/invited you to join Northbank Talent as a recruiter\./)).toBeVisible()
  await expect(page.getByLabel("Email")).toHaveValue(address)
  await expect(page).toHaveURL(/\/accept-invite$/)
  await checkScreen(page, testInfo, "auth-06-accept-invite")

  await page.getByLabel("Your name").fill("Nia Okafor")
  await page.getByLabel("Password", { exact: true }).fill("a long enough passphrase")
  await page.getByRole("button", { name: "Join Northbank Talent" }).click()

  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible()
  await expect(page.getByText(/^Good (morning|afternoon|evening), Nia$/)).toBeVisible()
  await expect(userMenu(page, /Nia Okafor/)).toContainText("Recruiter · Northbank Talent")
})

test("an unknown or used invitation link says so", async ({ page }, testInfo) => {
  await page.goto("/accept-invite#token=not-a-real-invitation-token")

  await expect(page.getByRole("heading", { level: 1, name: "This invitation can't be used any more" })).toBeVisible()
  await expect(page.getByText("Ask for a new invitation.")).toBeVisible()
  await checkScreen(page, testInfo, "auth-07-accept-invite-gone")
})
