import path from "node:path"

import AxeBuilder from "@axe-core/playwright"
import { expect, type Page, type TestInfo } from "@playwright/test"

/** Where the key screens' screenshots go: e2e-results/screenshots/<theme>/<name>.png. */
export const SCREENSHOT_DIR = path.join(__dirname, "..", "..", "e2e-results", "screenshots")

const THEMES = ["light", "dark"] as const
type Theme = (typeof THEMES)[number]

/** Axe impacts that fail the run (EPIC-01 §9); minor and moderate ones are attached to the report only. */
const BLOCKING_IMPACTS = new Set(["serious", "critical"])

/**
 * Waits until the page has nothing loading: no skeleton or busy region
 * (`aria-busy="true"`), so screenshots and axe see the finished screen.
 */
export async function settled(page: Page): Promise<void> {
  await expect(page.locator("[aria-busy='true']")).toHaveCount(0)
}

async function applyTheme(page: Page, theme: Theme): Promise<void> {
  // The app follows the system theme by default (next-themes, class on <html>).
  await page.emulateMedia({ colorScheme: theme })
  await expect(page.locator("html")).toHaveClass(new RegExp(`(^|\\s)${theme}(\\s|$)`))
}

/** Runs axe on the page as it is; fails on serious or critical violations. */
async function expectNoSeriousA11yViolations(page: Page, testInfo: TestInfo, label: string): Promise<void> {
  const results = await new AxeBuilder({ page }).analyze()
  await testInfo.attach(`axe-${label}.json`, {
    body: JSON.stringify(results.violations, null, 2),
    contentType: "application/json",
  })
  const blocking = results.violations.filter((violation) => BLOCKING_IMPACTS.has(violation.impact ?? ""))
  const summary = blocking.map(
    (violation) =>
      `${violation.impact} ${violation.id}: ${violation.help} (${violation.helpUrl})\n` +
      violation.nodes.map((node) => `    ${node.target.join(" ")}: ${node.failureSummary ?? ""}`).join("\n"),
  )
  // Soft: the flow goes on, so one run reports every page's violations; the test still fails.
  expect.soft(summary, `axe: serious or critical violations on ${label}`).toEqual([])
}

/**
 * The checks every E2E page gets (EPIC-01 §9), in light and in dark: axe with
 * no serious or critical violations (contrast differs per theme), and a
 * full-page screenshot for review (an artifact, not a pixel-diff gate). Leaves
 * the page in the light theme.
 */
export async function checkScreen(page: Page, testInfo: TestInfo, name: string): Promise<void> {
  await settled(page)
  for (const theme of THEMES) {
    await applyTheme(page, theme)
    await expectNoSeriousA11yViolations(page, testInfo, `${name}-${theme}`)
    await page.screenshot({
      path: path.join(SCREENSHOT_DIR, theme, `${name}.png`),
      fullPage: true,
      animations: "disabled",
      caret: "hide",
      // Toasts ("Campaign created") would cover part of the next screen.
      style: "[data-sonner-toaster] { display: none !important; }",
    })
  }
  await applyTheme(page, "light")
}
