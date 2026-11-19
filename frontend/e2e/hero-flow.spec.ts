import { readFile } from "node:fs/promises"

import { expect, test, type Page } from "@playwright/test"

import { checkScreen } from "./support/checks"
import { EXTRACTED_ROLE, EXTRACTED_SKILLS, heroCandidates, JOB_DESCRIPTION, newRunId } from "./support/hero-data"

/*
 * The hero flow (EPIC-01 §9), as a recruiter does it: create a campaign from a
 * job description, upload three resumes and watch them finish, filter the
 * ranked table, review a candidate and move them to a stage, open their
 * profile, export the shortlist. Every screen it visits gets axe (light and
 * dark) and a screenshot in both themes (`checkScreen`).
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

test("hero flow: campaign → uploads → ranked table → review → profile → export", async ({ page }, testInfo) => {
  const runId = newRunId()
  const title = `E2E ${runId} Python platform`
  const candidates = heroCandidates(runId)
  const [strong, moderate, weak] = candidates

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
    await checkScreen(page, testInfo, "02-new-campaign")

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
    await checkScreen(page, testInfo, "04-uploads")
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
    await expect(panel.getByRole("link", { name: strong.email })).toBeVisible()
    await expect(panel.getByText("Proceed to technical interview")).toBeVisible()

    const stage = panel.getByRole("combobox", { name: "Stage" })
    await expect(stage).toHaveText("Unreviewed")
    await checkScreen(page, testInfo, "03-candidates-panel")

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
    await checkScreen(page, testInfo, "05-person-profile")
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
})
