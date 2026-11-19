import { readFile } from "node:fs/promises"

import { expect, test, type Page } from "@playwright/test"

import { checkScreen } from "./support/checks"
import { EXTRACTED_ROLE, EXTRACTED_SKILLS, heroCandidates, JOB_DESCRIPTION, newRunId } from "./support/hero-data"

/*
 * The hero flow (EPIC-01 §9), as a recruiter does it: create a campaign from a
 * job description, upload three resumes and watch them finish, filter the
 * ranked table, review a candidate and move them to a stage, open their
 * profile, export the shortlist; then the same campaign on the dashboards:
 * its Insights, the people on Talent and the campaign on Home. Every screen it
 * visits gets axe (light and dark) and a screenshot in both themes
 * (`checkScreen`); `docs` marks the README's product-tour screenshots.
 *
 * It creates everything it asserts on (a new campaign, resumes with this run's
 * e-mails), so it does not depend on the seed or on earlier runs.
 */

function candidatesTable(page: Page) {
  return page.getByRole("table", { name: "Candidates" })
}

function candidateRows(page: Page) {
  return candidatesTable(page).locator("tbody").getByRole("row")
}

/** A link in the sidebar's main navigation (Home, Campaigns, Talent). */
function sidebarLink(page: Page, name: string) {
  return page.getByRole("list", { name: "Main" }).getByRole("link", { name })
}

test("hero flow: campaign → uploads → ranked table → review → profile → export → insights, talent, home", async ({
  page,
}, testInfo) => {
  const runId = newRunId()
  const title = `E2E ${runId} Python platform`
  const candidates = heroCandidates(runId)
  const [strong, moderate, weak] = candidates
  // The strong candidate's application and its campaign, read from the URL in step 4.
  let campaignId = ""
  let applicationId = ""

  await test.step("campaign list", async () => {
    await page.goto("/campaigns")
    await expect(page.getByRole("heading", { level: 1, name: "Campaigns" })).toBeVisible()
    await checkScreen(page, testInfo, "01-campaign-list")
    await page.getByRole("main").getByRole("link", { name: "New campaign" }).first().click()
    await expect(page).toHaveURL(/\/campaigns\/new$/)
  })

  await test.step("1. create a campaign with JD extraction", async () => {
    await expect(page.getByRole("heading", { level: 1, name: "New campaign" })).toBeVisible()
    await page.getByRole("textbox", { name: "Job description" }).fill(JOB_DESCRIPTION)
    await page.getByRole("button", { name: "Extract with AI" }).click()
    await expect(page.getByText(/^Filled in the role, 5 skills and the description\./)).toBeVisible()

    const form = page.getByRole("form", { name: "New campaign" })
    await expect(form.getByRole("textbox", { name: "Role" })).toHaveValue(EXTRACTED_ROLE)
    await expect(form.getByRole("list", { name: "Required skills" }).getByRole("listitem")).toHaveText(
      EXTRACTED_SKILLS,
    )
    await form.getByRole("textbox", { name: "Title" }).fill(title)
    await checkScreen(page, testInfo, "02-new-campaign", { docs: ["light"] })

    await form.getByRole("button", { name: "Create campaign" }).click()
    await expect(page).toHaveURL(/\/campaigns\/[0-9a-f-]{36}$/)
    await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible()
  })

  await test.step("2. upload 3 PDFs and see them reach Done", async () => {
    await page.getByRole("tab", { name: "Uploads" }).click()
    await expect(page).toHaveURL(/[?&]tab=uploads/)
    await page.getByLabel("PDF resumes").setInputFiles(
      candidates.map(({ filename, pdf }) => ({ name: filename, mimeType: "application/pdf", buffer: pdf })),
    )

    const resumes = page.getByRole("list", { name: "Resumes of this campaign" })
    for (const { filename } of candidates) {
      const row = resumes.getByRole("listitem").filter({ hasText: filename })
      // Processing runs in the workers; the row turns Done through the live (SSE) updates.
      await expect(row.getByText("Done", { exact: true })).toBeVisible({ timeout: 60_000 })
      await expect(row.getByRole("link", { name: "View candidate" })).toBeVisible()
    }
    await expect(page.getByText("3 resumes", { exact: true })).toBeVisible()
    await checkScreen(page, testInfo, "04-uploads", { docs: ["light"] })
  })

  await test.step("3. ranked candidates, filtered", async () => {
    await page.getByRole("tab", { name: "Candidates" }).click()
    await expect(candidateRows(page)).toHaveCount(3)
    // Ranked by score, best first.
    for (const [index, candidate] of [strong, moderate, weak].entries()) {
      const row = candidateRows(page).nth(index)
      await expect(row).toContainText(candidate.name)
      await expect(row).toContainText(String(candidate.score))
      await expect(row).toContainText(candidate.category)
    }

    // Filter by category: Moderate match only.
    await page.getByRole("button", { name: "Category" }).click()
    await page.getByRole("menuitemcheckbox", { name: "Moderate match" }).click()
    await page.keyboard.press("Escape")
    await expect(page).toHaveURL(/[?&]cat=moderate/)
    await expect(candidateRows(page)).toHaveCount(1)
    await expect(candidateRows(page).first()).toContainText(moderate.name)

    // Filters combine: searching for the strong candidate within Moderate matches finds no one.
    await page.getByRole("searchbox", { name: "Search candidates by name or email" }).fill(strong.name)
    await expect(page.getByText("No candidates match these filters")).toBeVisible()

    await page.getByRole("search", { name: "Filter candidates" }).getByRole("button", { name: "Clear filters" }).click()
    await expect(page).not.toHaveURL(/[?&](cat|q)=/)
    await expect(candidateRows(page)).toHaveCount(3)
  })

  await test.step("4. open the review panel and change the stage", async () => {
    await candidateRows(page).filter({ hasText: strong.name }).getByRole("cell").nth(2).click()
    const panel = page.getByRole("dialog", { name: strong.name })
    await expect(panel).toBeVisible()
    await expect(page).toHaveURL(/[?&]open=[0-9a-f-]{36}/)
    const url = new URL(page.url())
    campaignId = url.pathname.split("/").at(-1) ?? ""
    applicationId = url.searchParams.get("open") ?? ""
    await expect(panel.getByRole("link", { name: strong.email })).toBeVisible()
    await expect(panel.getByText("Proceed to technical interview")).toBeVisible()

    const stage = panel.getByRole("combobox", { name: "Stage" })
    await expect(stage).toHaveText("Unreviewed")
    await checkScreen(page, testInfo, "03-candidates-panel", { docs: ["light", "dark"] })

    await stage.click()
    await page.getByRole("option", { name: "Phone call" }).click()
    await expect(stage).toHaveText("Phone call")
    // Optimistic in the table too …
    await expect(candidateRows(page).filter({ hasText: strong.name })).toContainText("Phone call")
    // … and saved: a reload reads it back from the API (the panel reopens from the URL).
    await page.reload()
    await expect(page.getByRole("dialog", { name: strong.name }).getByRole("combobox", { name: "Stage" })).toHaveText(
      "Phone call",
    )
  })

  await test.step("5. open the Person profile", async () => {
    await page.getByRole("dialog", { name: strong.name }).getByRole("link", { name: "Open full profile" }).click()
    await expect(page).toHaveURL(/\/people\/[0-9a-f-]{36}\?application=[0-9a-f-]{36}$/)
    await expect(page.getByRole("heading", { level: 1, name: strong.name })).toBeVisible()
    const application = page
      .getByRole("table", { name: "Applications" })
      .getByRole("row")
      .filter({ hasText: title })
    await expect(application).toContainText("Phone call")
    await expect(application).toContainText(strong.category)
    await checkScreen(page, testInfo, "05-person-profile", { docs: ["light"] })
  })

  await test.step("6. export CSV", async () => {
    await page.getByRole("table", { name: "Applications" }).getByRole("link", { name: title }).click()
    await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible()
    await page.getByRole("dialog", { name: strong.name }).getByRole("button", { name: "Close" }).click()
    await expect(page.getByRole("dialog")).toHaveCount(0)

    const download = page.waitForEvent("download")
    await page.getByRole("button", { name: "Export CSV" }).click()
    const file = await download
    expect(file.suggestedFilename()).toMatch(/^applications_[0-9a-f-]{36}\.csv$/)

    const csv = await readFile(await file.path(), "utf-8")
    const [header, ...lines] = csv.trim().split(/\r?\n/)
    expect(header).toMatch(/^Name,Email,Phone,Score,Category,Pipeline Stage,/)
    expect(lines).toHaveLength(3)
    // In the table's order (score, best first), with the stage just set.
    expect(lines[0]).toContain(`${strong.name},${strong.email}`)
    expect(lines[0]).toContain("phone_call")
    expect(lines[1]).toContain(`${moderate.name},${moderate.email}`)
    expect(lines[2]).toContain(`${weak.name},${weak.email}`)
  })

  await test.step("7. the campaign's Insights", async () => {
    await page.getByRole("tab", { name: "Insights" }).click()
    await expect(page).toHaveURL(/[?&]tab=insights/)
    const categories = page.getByRole("region", { name: "Category distribution" })
    await expect(categories).toContainText("3 applications: 1 strong, 1 moderate and 1 weak matches, 0 rejected.")
    await expect(page.getByRole("region", { name: "Processing funnel" })).toContainText(
      "3 uploaded, 3 processed, 3 scored, 1 reviewed.",
    )
    await checkScreen(page, testInfo, "06-insights", { docs: ["light"] })

    // The chart's data as a table (the keyboard and screen-reader route).
    const toggle = categories.getByRole("button", { name: "Table for category distribution" })
    await toggle.click()
    await expect(toggle).toHaveAttribute("aria-pressed", "true")
    const strongRow = categories.getByRole("table", { name: "Category distribution" }).getByRole("row", {
      name: /Strong match/,
    })
    await expect(strongRow).toBeVisible()
    await expect(strongRow).toContainText("1 (33%)")
  })

  await test.step("an old /candidates/<id> link opens the application in its campaign", async () => {
    await page.goto(`/candidates/${applicationId}`)
    await expect(page).toHaveURL(
      (url) => url.pathname === `/campaigns/${campaignId}` && url.search === `?open=${applicationId}`,
    )
    const panel = page.getByRole("dialog", { name: strong.name })
    await expect(panel).toBeVisible()
    // The panel is modal: close it before using the sidebar.
    await panel.getByRole("button", { name: "Close" }).click()
    await expect(page.getByRole("dialog")).toHaveCount(0)
  })

  await test.step("8. Talent, searched by the run's e-mails", async () => {
    await sidebarLink(page, "Talent").click()
    await expect(page.getByRole("heading", { level: 1, name: "Talent" })).toBeVisible()
    await checkScreen(page, testInfo, "07-talent", { docs: ["light"] })

    await page.getByRole("searchbox", { name: "Search people by name or email" }).fill(runId)
    await expect(page).toHaveURL(new RegExp(`[?&]q=${runId}`))
    const people = page.getByRole("table", { name: /^People/ }).locator("tbody").getByRole("row")
    await expect(people).toHaveCount(3)
    await expect(people.filter({ hasText: strong.email })).toContainText(strong.name)

    await people.getByRole("link", { name: strong.name }).click()
    await expect(page).toHaveURL(/\/people\/[0-9a-f-]{36}$/)
    await expect(page.getByRole("heading", { level: 1, name: strong.name })).toBeVisible()
  })

  await test.step("9. Home shows the campaign and its strong match", async () => {
    await sidebarLink(page, "Home").click()
    await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible()
    await expect(page.getByRole("region", { name: "Active campaigns" }).getByRole("link", { name: title })).toBeVisible()
    // The row of this run's campaign (earlier runs on a kept stack have their own Asha Verma).
    const match = page
      .getByRole("region", { name: "Recent strong matches" })
      .getByRole("row")
      .filter({ hasText: title })
    await expect(match).toContainText(strong.name)
    await expect(match.getByRole("link", { name: strong.name })).toHaveAttribute(
      "href",
      `/campaigns/${campaignId}?open=${applicationId}`,
    )
    await checkScreen(page, testInfo, "08-home", { docs: ["light", "dark"] })
  })
})
