import { defineConfig, devices } from "@playwright/test"

import { RECRUITER_STATE } from "./e2e/support/users"

/*
 * End-to-end tests (EPIC-01 §9): the hero flow in Chromium against the Compose
 * stack with the fake AI provider. Start the stack with `make e2e` (or
 * scripts/e2e.sh), which also runs these; on its own, `npm run e2e` expects the
 * stack at E2E_BASE_URL (default http://localhost:13000, the E2E project's port).
 *
 * Everything a run writes goes to e2e-results/ (gitignored, uploaded by CI):
 * the HTML report, traces and failure screenshots (artifacts/), and the
 * light/dark screenshots of the key screens (screenshots/).
 *
 * No fixed sleeps: web-first assertions and auto-waiting only. One retry in CI,
 * none locally, so a flaky step shows up locally rather than being retried away.
 *
 * Signed in (EPIC-02 §11.4): the `setup` project signs in once through the
 * login page as a seeded recruiter and saves the browser state; the specs
 * start from it. `auth.spec.ts` starts signed out instead.
 */
const CI = !!process.env.CI

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./e2e-results/artifacts",
  // One worker: the specs share one stack, and the hero flow is a single journey.
  workers: 1,
  fullyParallel: false,
  forbidOnly: CI,
  retries: CI ? 1 : 0,
  // The hero flow uploads and processes three resumes.
  timeout: 120_000,
  expect: { timeout: 10_000 },
  reporter: [
    [CI ? "github" : "list"],
    ["html", { outputFolder: "./e2e-results/report", open: "never" }],
  ],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:13000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    // Fixed so screenshots and dates are comparable between runs and machines.
    viewport: { width: 1440, height: 900 },
    locale: "en-GB",
    timezoneId: "UTC",
    colorScheme: "light",
  },
  projects: [
    { name: "setup", testMatch: /auth\.setup\.ts$/, use: { ...devices["Desktop Chrome"] } },
    {
      name: "chromium",
      dependencies: ["setup"],
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, storageState: RECRUITER_STATE },
    },
  ],
})
