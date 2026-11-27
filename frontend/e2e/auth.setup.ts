import { mkdir } from "node:fs/promises"
import path from "node:path"

import { expect, test as setup } from "@playwright/test"

import { RECRUITER_STATE, signIn, USERS } from "./support/users"

/*
 * Signs in once, through the real login page, as the hero flow's recruiter
 * (EPIC-02 §11.4), and saves the browser state (the session and CSRF
 * cookies). The other specs start from it (`storageState` in
 * playwright.config.ts), so they run signed in without signing in again.
 */
setup("sign in as the recruiter", async ({ page }) => {
  await page.goto("/")
  await expect(page).toHaveURL(/\/login$/)
  await signIn(page, USERS.recruiter)
  await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible()

  await mkdir(path.dirname(RECRUITER_STATE), { recursive: true })
  await page.context().storageState({ path: RECRUITER_STATE })
})
