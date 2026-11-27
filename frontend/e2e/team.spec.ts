import { expect, test, type Browser, type Page } from "@playwright/test"

import { checkScreen } from "./support/checks"
import { newRunId, PITCH } from "./support/hero-data"
import { latestEmailId, waitForLink } from "./support/mailpit"
import { apiSession, signIn, USERS } from "./support/users"

/*
 * Tenancy and the team in the UI (EPIC-02 §9, E2-10), kept to two journeys;
 * the full roles and isolation suite is E2-11's:
 *
 * 1. An owner adds a client and a campaign for it, then invites a recruiter
 *    limited to that client from Settings → Team. The invitee reads the email
 *    in Mailpit, joins, and sees only that client's campaigns. Invited to a
 *    second agency, they switch between the two in the sidebar.
 * 2. A viewer sees no write actions, and a direct URL to one says why.
 *
 * Each test starts signed out and signs in with its own seeded account (or a
 * new one), so none ends another's session. Every new screen gets axe and
 * screenshots in both themes (`checkScreen`); `docs` marks the README's.
 */
test.use({ storageState: { cookies: [], origins: [] } })

/** The sidebar's agency and client switcher. */
function switcher(page: Page) {
  return page.getByRole("button", { name: /change the agency or client/ })
}

async function signedIn(page: Page, email: string): Promise<void> {
  await page.goto("/login")
  await signIn(page, email)
  await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible()
}

/** A new, signed-out browser for the invitee. */
async function newPage(browser: Browser): Promise<Page> {
  const context = await browser.newContext({ storageState: { cookies: [], origins: [] } })
  return context.newPage()
}

test("an owner invites a recruiter limited to one client, who then sees only that client's campaigns", async ({
  page,
  browser,
}, testInfo) => {
  // A run that refreshes the README's screenshots (a fresh stack) uses demo names, as the hero flow does.
  const runId = newRunId()
  const clientName = PITCH ? "Finch Pay" : `Finch Pay ${runId}`
  const ownTitle = PITCH ? "Payments Engineer, card issuing" : `E2E ${runId} payments engineer`
  const otherTitle = PITCH ? "Operations Lead" : `E2E ${runId} general ops lead`
  const address = PITCH ? "kofi.mensah@finchpay.example" : `e2e-team-${runId}@northbank.example`

  await test.step("the owner adds a client in Settings → Clients", async () => {
    await signedIn(page, USERS.northbankOwner)
    await expect(switcher(page)).toContainText("Northbank Talent")
    await expect(switcher(page)).toContainText("All clients")

    await page.getByRole("link", { name: "Settings" }).click()
    await expect(page).toHaveURL(/\/settings\/profile$/)
    await checkScreen(page, testInfo, "10-settings-profile")

    await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "Clients" }).click()
    await page.getByRole("button", { name: "New client" }).click()
    const dialog = page.getByRole("dialog", { name: "New client" })
    await dialog.getByRole("textbox", { name: "Name" }).fill(clientName)
    await dialog.getByRole("button", { name: "Add client" }).click()
    await expect(dialog).toBeHidden()
    await expect(page.getByRole("table").getByRole("row").filter({ hasText: clientName })).toBeVisible()
    await checkScreen(page, testInfo, "11-settings-clients")
  })

  await test.step("a campaign for the new client (the form), and one for General (the API)", async () => {
    await page.goto("/campaigns/new")
    const form = page.getByRole("form", { name: "New campaign" })
    // Two active clients now: the form asks which one.
    await form.getByRole("combobox", { name: "Client" }).click()
    await page.getByRole("option", { name: clientName }).click()
    await form.getByRole("textbox", { name: "Title" }).fill(ownTitle)
    await form.getByRole("textbox", { name: "Role" }).fill("Payments Engineer")
    await form.getByRole("button", { name: "Create campaign" }).click()
    await expect(page.getByRole("heading", { level: 1, name: ownTitle })).toBeVisible()
    // The client sits above the title.
    await expect(page.getByText(`Client: ${clientName}`, { exact: true })).toBeVisible()

    const { api, csrfToken } = await apiSession(USERS.northbankOwner)
    const created = await api.post("campaigns", {
      data: { title: otherTitle, role: "Operations Lead" },
      headers: { "X-CSRF-Token": csrfToken },
    })
    expect(created.status(), await created.text()).toBe(201)
    await api.dispose()

    await page.goto("/campaigns")
    const table = page.getByRole("table")
    await expect(table.getByRole("row").filter({ hasText: ownTitle })).toContainText(clientName)
    await expect(table.getByRole("row").filter({ hasText: otherTitle })).toContainText("General")
  })

  await test.step("Settings → Team: invite a recruiter limited to that client", async () => {
    await page.goto("/settings/team")
    await expect(page.getByRole("heading", { level: 2, name: "Members" })).toBeVisible()
    await page.getByRole("button", { name: "Invite a teammate" }).click()
    const dialog = page.getByRole("dialog", { name: "Invite a teammate" })
    await dialog.getByRole("textbox", { name: "Email" }).fill(address)
    await dialog.getByRole("radio", { name: "Recruiter" }).check()
    await dialog.getByRole("radio", { name: "Only some clients" }).check()
    await dialog.getByRole("checkbox", { name: clientName }).check()
    await checkScreen(page, testInfo, "12-invite-dialog")
    await dialog.getByRole("button", { name: "Send invitation" }).click()
    await expect(dialog).toBeHidden()

    const pending = page.getByRole("list", { name: "Pending invitations" }).getByRole("listitem").filter({ hasText: address })
    await expect(pending).toContainText(`Recruiter · ${clientName}`)
    await checkScreen(page, testInfo, "13-settings-team", { docs: ["light"] })
  })

  const invitee = await newPage(browser)
  await test.step("the invitee joins from the email and sees only that client's campaigns", async () => {
    const link = await waitForLink(address, "/accept-invite", null)
    await invitee.goto(link)
    await expect(invitee.getByRole("heading", { level: 1, name: "Join Northbank Talent" })).toBeVisible()
    await invitee.getByLabel("Your name").fill("Kofi Mensah")
    await invitee.getByLabel("Password", { exact: true }).fill("a long enough passphrase")
    await invitee.getByRole("button", { name: "Join Northbank Talent" }).click()
    await expect(invitee.getByRole("heading", { level: 1, name: "Home" })).toBeVisible()
    await expect(switcher(invitee)).toContainText("All your clients")

    await invitee.goto("/campaigns")
    const table = invitee.getByRole("table")
    await expect(table.getByRole("row").filter({ hasText: ownTitle })).toBeVisible()
    await expect(table.getByRole("row").filter({ hasText: otherTitle })).toHaveCount(0)
    // A recruiter can create campaigns, but has no Team or Clients settings.
    await expect(invitee.getByRole("main").getByRole("link", { name: "New campaign" })).toBeVisible()
    await invitee.goto("/settings/team")
    await expect(invitee.getByRole("heading", { name: "You don't have access to this page" })).toBeVisible()
  })

  await test.step("the owner sees the new member with their one client", async () => {
    await page.reload()
    const row = page.getByRole("row").filter({ hasText: address })
    await expect(row).toContainText("Kofi Mensah")
    await expect(row).toContainText(clientName)
    await expect(row.getByRole("combobox", { name: "Role of Kofi Mensah" })).toHaveText("Recruiter")
  })

  await test.step("in a second agency, the switcher changes agency and starts afresh", async () => {
    // The default agency's owner invites the same address; signed in as it, joining takes one click.
    const before = await latestEmailId(address)
    const { api, csrfToken } = await apiSession(USERS.defaultOwner)
    const invited = await api.post("team/invitations", {
      data: { email: address, role: "viewer", all_clients: true, client_ids: [] },
      headers: { "X-CSRF-Token": csrfToken },
    })
    expect(invited.status(), await invited.text()).toBe(201)
    await api.dispose()
    const link = await waitForLink(address, "/accept-invite", before)
    await invitee.goto(link)
    await invitee.getByRole("button", { name: "Join Default agency" }).click()
    await expect(invitee.getByRole("heading", { level: 1, name: "Home" })).toBeVisible()
    await expect(switcher(invitee)).toContainText("Default agency")

    await switcher(invitee).click()
    const menu = invitee.getByRole("menu")
    await expect(menu.getByRole("menuitemradio", { name: /Default agency/ })).toHaveAttribute("aria-checked", "true")
    await checkScreen(invitee, testInfo, "14-agency-switcher", { docs: ["light"] })
    await menu.getByRole("menuitemradio", { name: /Northbank Talent/ }).click()

    await expect(switcher(invitee)).toContainText("Northbank Talent")
    await expect(invitee.getByRole("heading", { level: 1, name: "Home" })).toBeVisible()
    await invitee.goto("/campaigns")
    await expect(invitee.getByRole("table").getByRole("row").filter({ hasText: ownTitle })).toBeVisible()
  })
  await invitee.context().close()
})

test("a viewer sees no write actions, and a direct URL to one says so", async ({ page }, testInfo) => {
  await signedIn(page, USERS.defaultViewer)
  await expect(page.getByRole("main").getByRole("link", { name: "New campaign" })).toHaveCount(0)

  await page.goto("/campaigns")
  await expect(page.getByRole("heading", { level: 1, name: "Campaigns" })).toBeVisible()
  await expect(page.getByRole("main").getByRole("link", { name: "New campaign" })).toHaveCount(0)

  // A seeded campaign with candidates: read, but nothing to change.
  await page.getByRole("table").getByRole("link").first().click()
  await expect(page.getByRole("table", { name: "Candidates" })).toBeVisible()
  await expect(page.getByRole("button", { name: "Upload resumes" })).toHaveCount(0)
  await expect(page.getByRole("button", { name: "Edit brief" })).toHaveCount(0)
  await expect(page.getByRole("button", { name: /^Export/ })).toHaveCount(0)
  await expect(page.getByRole("checkbox")).toHaveCount(0)
  await page.getByRole("table", { name: "Candidates" }).locator("tbody tr").first().click()
  const panel = page.locator("[data-panel='review']")
  await expect(panel.getByRole("link", { name: "Open full profile" })).toBeVisible()
  await expect(panel.getByRole("button", { name: "Not a fit" })).toHaveCount(0)
  await expect(panel.getByRole("combobox", { name: "Stage" })).toHaveCount(0)
  await expect(panel.getByRole("textbox", { name: "Notes" })).toHaveCount(0)
  await checkScreen(page, testInfo, "15-viewer-campaign")
  await page.keyboard.press("Escape")

  await page.getByRole("tab", { name: "Uploads" }).click()
  await expect(page.getByText("Recruiters, admins and owners upload them.")).toBeVisible()
  await expect(page.getByRole("button", { name: "Choose PDFs" })).toHaveCount(0)
  await page.getByRole("tab", { name: "Brief" }).click()
  await expect(page.getByRole("heading", { name: "The brief" })).toBeVisible()
  await expect(page.getByRole("button", { name: "Save changes" })).toHaveCount(0)
  await expect(page.getByRole("button", { name: "Delete campaign" })).toHaveCount(0)

  await page.goto("/campaigns/new")
  await expect(page.getByRole("heading", { name: "You don't have access to this page" })).toBeVisible()
  await expect(page.getByText(/As a viewer in Default agency, you can't create campaigns\./)).toBeVisible()

  await page.goto("/settings/team")
  await expect(page.getByRole("heading", { name: "You don't have access to this page" })).toBeVisible()
  // Settings offers a viewer their profile only.
  const sections = page.getByRole("navigation", { name: "Settings sections" }).getByRole("link")
  await expect(sections).toHaveText(["Profile"])
  await checkScreen(page, testInfo, "16-viewer-no-access")
})
