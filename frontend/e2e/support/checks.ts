import path from "node:path"

import AxeBuilder from "@axe-core/playwright"
import { expect, type Page, type TestInfo } from "@playwright/test"

/** Where the key screens' screenshots go: e2e-results/screenshots/<theme>/<name>.png. */
export const SCREENSHOT_DIR = path.join(__dirname, "..", "..", "e2e-results", "screenshots")

const REPO_ROOT = path.join(__dirname, "..", "..", "..")

/**
 * The README's product-tour screenshots (docs/screenshots) are refreshed by a
 * run with E2E_SCREENSHOT_DIR set (relative to the repo root):
 * `E2E_SCREENSHOT_DIR=docs/screenshots make e2e`. Unset, nothing is written.
 */
const DOCS_SCREENSHOT_DIR = process.env.E2E_SCREENSHOT_DIR
  ? path.resolve(REPO_ROOT, process.env.E2E_SCREENSHOT_DIR)
  : undefined

const THEMES = ["light", "dark"] as const
export type Theme = (typeof THEMES)[number]

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
  // Reduced motion: sonner then shows a toast without its 400 ms fade-in, and
  // the app's own transitions are instant (globals.css), so axe never measures
  // the contrast of something half-faded.
  await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" })
  await expect(page.locator("html")).toHaveClass(new RegExp(`(^|\\s)${theme}(\\s|$)`))
  await toastsSettled(page)
}

/**
 * Every toast has entered: sonner marks it `data-mounted="true"` the frame
 * after it appears, and with reduced motion it is fully opaque from then on.
 * A toast caught before that is still transparent, and axe reported its text
 * as low contrast (an intermittent failure before E2-09).
 */
async function toastsSettled(page: Page): Promise<void> {
  await expect(page.locator("[data-sonner-toast][data-mounted='false']")).toHaveCount(0)
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

const SCREENSHOT_OPTIONS = {
  animations: "disabled",
  caret: "hide",
  // Toasts ("Campaign created") would cover part of the next screen.
  style: "[data-sonner-toaster] { display: none !important; }",
} as const

/**
 * The checks every E2E page gets (EPIC-01 §9), in light and in dark: axe with
 * no serious or critical violations (contrast differs per theme), and a
 * full-page screenshot for review (an artifact, not a pixel-diff gate). Leaves
 * the page in the light theme.
 *
 * `docs` lists the themes in which the screen is also one of the README's
 * screenshots: cropped to the viewport, as `<name without NN->-<theme>.png`
 * in E2E_SCREENSHOT_DIR (when set).
 */
export async function checkScreen(
  page: Page,
  testInfo: TestInfo,
  name: string,
  { docs = [] }: { docs?: readonly Theme[] } = {},
): Promise<void> {
  await settled(page)
  for (const theme of THEMES) {
    await applyTheme(page, theme)
    await expectNoSeriousA11yViolations(page, testInfo, `${name}-${theme}`)
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, theme, `${name}.png`), fullPage: true, ...SCREENSHOT_OPTIONS })
    if (DOCS_SCREENSHOT_DIR && docs.includes(theme)) {
      const file = `${name.replace(/^\d+-/, "")}-${theme}.png`
      await page.screenshot({ path: path.join(DOCS_SCREENSHOT_DIR, file), ...SCREENSHOT_OPTIONS })
    }
  }
  await applyTheme(page, "light")
}
