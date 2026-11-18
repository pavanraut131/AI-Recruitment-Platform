import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import type { UrlUpdateEvent } from "nuqs/adapters/testing"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { Schema } from "@/lib/api/client"
import { clearUnsavedDrafts } from "@/lib/use-autosave"
import { deferred, installDomPolyfills, problem, renderWithProviders } from "@/test/helpers"
import { makePerson, makePersonApplication } from "@/test/people"

import { PersonProfile } from "./person-profile"
import { installPeopleApi, makeApplicationDetail } from "./test-utils"

const { toast } = vi.hoisted(() => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}))
vi.mock("sonner", () => ({ toast }))

beforeEach(() => {
  installDomPolyfills()
  toast.error.mockClear()
})
afterEach(() => {
  clearUnsavedDrafts()
})

// Notes autosave waits 800 ms after the last key press: real timers, longer waits, own timeout.
const AUTOSAVE_TEST_TIMEOUT_MS = 15_000
const SAVE_WAIT = { timeout: 4_000 }

const PERSON_ID = "aaaaaaaa-0000-4000-8000-000000000001"
const FRONTEND = { id: "c0000000-0000-4000-8000-000000000001", title: "Senior frontend engineer" }
const PLATFORM = { id: "c0000000-0000-4000-8000-000000000002", title: "Data platform lead", status: "closed" as const }

const person = makePerson({
  id: PERSON_ID,
  name: "Ada Lovelace",
  github_url: "github.com/ada",
  linkedin_url: "https://linkedin.com/in/ada",
  identifiers: [
    { kind: "email", value: "ada@example.com", inferred: false },
    { kind: "phone", value: "+44 20 7946 0000", inferred: false },
  ],
  profile: {
    skills: ["React", "TypeScript", "Python"],
    years_of_experience: 7.5,
    work_experience: [
      { role: "Staff engineer", company: "Analytical Engines Ltd", duration: "2021–now", description: "Leads the UI team." },
      { company: null, duration: "2019" } as never,
    ],
    education: [{ degree: "BSc Mathematics", institution: "University of London", year: "2015" }],
    projects: [{ name: "Difference engine", description: "A calculator", technologies: ["Rust", "WASM"] }],
    certifications: ["AWS Certified Developer"],
  } as unknown as Schema<"Person">["profile"],
  applications: [
    makePersonApplication("app-new", FRONTEND, {
      score: 86,
      category: "strong_match",
      pipeline_stage: "technical",
      created_at: "2026-11-10T09:00:00Z",
    }),
    makePersonApplication("app-old", PLATFORM, {
      score: 58,
      category: "moderate_match",
      pipeline_stage: null,
      created_at: "2026-09-02T09:00:00Z",
    }),
  ],
})

const newApplication = makeApplicationDetail("app-new", {
  campaignId: FRONTEND.id,
  personId: PERSON_ID,
  score: 86,
  category: "strong_match",
  score_breakdown: {
    skills_match: 36,
    experience_match: 28,
    project_relevance: 12,
    education_bonus: 7,
    communication_quality: 3,
  } as unknown as Schema<"Application">["score_breakdown"],
  recommendation: "Proceed to technical interview",
  strengths: ["Strong React"],
  pipeline_stage: "technical",
  notes: "Great call.",
})
const oldApplication = makeApplicationDetail("app-old", {
  campaignId: PLATFORM.id,
  personId: PERSON_ID,
  score: 58,
  category: "moderate_match",
  recommendation: "Phone screen first",
  notes: null,
})

function renderProfile(searchParams = "") {
  const onUrlUpdate = vi.fn<(event: UrlUpdateEvent) => void>()
  const result = renderWithProviders(<PersonProfile personId={PERSON_ID} />, { searchParams, onUrlUpdate })
  const lastQuery = () => onUrlUpdate.mock.lastCall?.[0].queryString
  return { ...result, onUrlUpdate, lastQuery }
}

const section = (name: string) => screen.getByRole("region", { name })
const evaluation = () => section("Evaluation")

async function chooseFrom(combobox: HTMLElement, option: string | RegExp) {
  fireEvent.keyDown(combobox, { key: "Enter" })
  const listbox = await screen.findByRole("listbox")
  fireEvent.click(within(listbox).getByRole("option", { name: option }))
}

describe("PersonProfile: header and sections", () => {
  it("shows the person, their contact and links, and the most recent application by default", async () => {
    const api = installPeopleApi({ people: [person], applications: [newApplication, oldApplication] })
    renderProfile()

    expect(await screen.findByRole("heading", { level: 1, name: "Ada Lovelace" })).toBeTruthy()
    const contact = screen.getByRole("list", { name: "Contact and links" })
    expect(within(contact).getByRole("link", { name: "ada@example.com" }).getAttribute("href")).toBe("mailto:ada@example.com")
    expect(within(contact).getByRole("link", { name: "+44 20 7946 0000" }).getAttribute("href")).toBe("tel:+442079460000")
    expect(within(contact).getByRole("link", { name: /GitHub/ }).getAttribute("href")).toBe("https://github.com/ada")
    expect(within(contact).getByRole("link", { name: /LinkedIn/ }).getAttribute("href")).toBe("https://linkedin.com/in/ada")
    expect(contact.textContent).toContain("7.5 yrs of experience")

    expect(screen.getByRole("combobox", { name: /Application/ }).textContent).toContain("Senior frontend engineer")
    expect(await within(evaluation()).findByText("Proceed to technical interview")).toBeTruthy()
    expect(within(evaluation()).getAllByText(/36/).length).toBeGreaterThan(0)
    expect(api.callsTo("GET", /^\/applications\//).map((call) => call.path)).toEqual(["/applications/app-new"])
  })

  it("shows the extracted profile, leaving out entries it cannot read", async () => {
    installPeopleApi({ people: [person], applications: [newApplication, oldApplication] })
    renderProfile()
    await screen.findByRole("heading", { level: 1, name: "Ada Lovelace" })
    const profile = within(section("Profile"))
    expect(profile.getByText("React")).toBeTruthy()
    const work = within(profile.getByRole("list", { name: "Work experience" }))
    expect(work.getAllByRole("listitem")).toHaveLength(1)
    expect(work.getByText("Staff engineer")).toBeTruthy()
    expect(work.getByText("Analytical Engines Ltd")).toBeTruthy()
    expect(profile.getByText("BSc Mathematics")).toBeTruthy()
    expect(profile.getByText("Rust · WASM")).toBeTruthy()
    expect(profile.getByText("AWS Certified Developer")).toBeTruthy()
  })

  it("lists every application with campaign, score, category, stage and date", async () => {
    installPeopleApi({ people: [person], applications: [newApplication, oldApplication] })
    renderProfile()
    const table = await screen.findByRole("table", { name: "Applications" })
    const rows = within(table).getAllByRole("row").slice(1)
    expect(rows).toHaveLength(2)
    const [first, second] = rows
    expect(within(first).getByRole("link", { name: "Senior frontend engineer" }).getAttribute("href")).toBe(
      `/campaigns/${FRONTEND.id}?open=app-new`,
    )
    expect(first.textContent).toContain("86")
    expect(first.textContent).toContain("Strong match")
    expect(first.textContent).toContain("Technical")
    expect(first.textContent).toContain("10 Nov 2026")
    expect(first.getAttribute("aria-current")).toBe("true")
    expect(second.textContent).toContain("Moderate match")
    expect(second.textContent).toContain("Unreviewed")
    expect(second.textContent).toContain("Campaign closed")
  })

  it("explains a person with no applications", async () => {
    installPeopleApi({ people: [{ ...person, applications: [] }], applications: [] })
    renderProfile()
    await screen.findByRole("heading", { level: 1, name: "Ada Lovelace" })
    expect(within(evaluation()).getByText(/No applications/)).toBeTruthy()
    expect(screen.queryByRole("combobox", { name: /Application/ })).toBeNull()
  })

  it("shows unresolved identity conflicts as an info banner", async () => {
    const conflicted = {
      ...person,
      has_unresolved_conflicts: true,
      conflicts: [
        {
          id: "conflict-1",
          role: "chosen" as const,
          resume_id: "resume-app-old",
          other_person_id: "bbbbbbbb-0000-4000-8000-000000000002",
          reason: "email matched one person and phone matched another",
          created_at: "2026-11-02T09:00:00Z",
          resolved_at: null,
        },
        {
          id: "conflict-2",
          role: "conflicting" as const,
          resume_id: "resume-x",
          other_person_id: "cccccccc-0000-4000-8000-000000000003",
          reason: "resolved already",
          created_at: "2026-10-02T09:00:00Z",
          resolved_at: "2026-10-03T09:00:00Z",
        },
      ],
    }
    installPeopleApi({ people: [conflicted], applications: [newApplication, oldApplication] })
    renderProfile()
    const banner = await screen.findByRole("complementary", { name: "Identity conflicts" })
    expect(banner.textContent).toContain("The resume for “Data platform lead” shared a phone number with another person.")
    const links = within(banner).getAllByRole("link", { name: "View the other person" })
    expect(links).toHaveLength(1)
    expect(links[0].getAttribute("href")).toBe("/people/bbbbbbbb-0000-4000-8000-000000000002")
  })

  it("has no banner without unresolved conflicts", async () => {
    installPeopleApi({ people: [person], applications: [newApplication, oldApplication] })
    renderProfile()
    await screen.findByRole("heading", { level: 1, name: "Ada Lovelace" })
    expect(screen.queryByRole("complementary", { name: "Identity conflicts" })).toBeNull()
  })
})

describe("PersonProfile: application switcher", () => {
  it("switches the evaluation and notes, and keeps the choice in the URL", async () => {
    const api = installPeopleApi({ people: [person], applications: [newApplication, oldApplication] })
    const { lastQuery } = renderProfile()
    await within(await screen.findByRole("region", { name: "Evaluation" })).findByText("Proceed to technical interview")

    await chooseFrom(screen.getByRole("combobox", { name: /Application/ }), /Data platform lead/)
    expect(await within(evaluation()).findByText("Phone screen first")).toBeTruthy()
    expect(lastQuery()).toBe("?application=app-old")
    expect(api.callsTo("GET", /^\/applications\//).at(-1)?.path).toBe("/applications/app-old")
    const notes = within(section("Notes")).getByRole("textbox", { name: /Notes/ }) as HTMLTextAreaElement
    expect(notes.value).toBe("")
    // The stage selector follows the selected application.
    expect(screen.getByRole("combobox", { name: "Stage" }).textContent).toContain("Unreviewed")

    // Back to the most recent: the default leaves the URL.
    fireEvent.click(within(section("Applications")).getByRole("button", { name: /Senior frontend engineer/ }))
    expect(await within(evaluation()).findByText("Proceed to technical interview")).toBeTruthy()
    expect(lastQuery()).toBe("")
  })

  it("opens on the application in the URL, and falls back to the most recent for an unknown one", async () => {
    installPeopleApi({ people: [person], applications: [newApplication, oldApplication] })
    const first = renderProfile("?application=app-old")
    expect(await within(await screen.findByRole("region", { name: "Evaluation" })).findByText("Phone screen first")).toBeTruthy()
    first.unmount()

    renderProfile("?application=nope")
    expect(
      await within(await screen.findByRole("region", { name: "Evaluation" })).findByText("Proceed to technical interview"),
    ).toBeTruthy()
  })
})

describe("PersonProfile: stage and notes", () => {
  it("changes the stage optimistically and rolls back on error", async () => {
    const response = deferred<Response>()
    installPeopleApi({
      people: [person],
      applications: [newApplication, oldApplication],
      override: ({ method }) => (method === "PATCH" ? response.promise : undefined),
    })
    renderProfile()
    await screen.findByRole("heading", { level: 1, name: "Ada Lovelace" })
    const firstRow = () => within(screen.getByRole("table", { name: "Applications" })).getAllByRole("row")[1]
    expect(firstRow().textContent).toContain("Technical")

    await chooseFrom(screen.getByRole("combobox", { name: "Stage" }), "Offer")
    await waitFor(() => expect(firstRow().textContent).toContain("Offer"))
    response.resolve(problem(500, "Internal Server Error"))
    await waitFor(() => expect(firstRow().textContent).toContain("Technical"))
    expect(toast.error).toHaveBeenCalled()
  })

  it("focuses the stage selector with S", async () => {
    installPeopleApi({ people: [person], applications: [newApplication, oldApplication] })
    renderProfile()
    await screen.findByRole("heading", { level: 1, name: "Ada Lovelace" })
    fireEvent.keyDown(document.body, { key: "s" })
    expect(document.activeElement).toBe(screen.getByRole("combobox", { name: "Stage" }))
  })

  it(
    "autosaves the selected application's notes",
    async () => {
      const api = installPeopleApi({ people: [person], applications: [newApplication, oldApplication] })
      renderProfile()
      const notesSection = within(await screen.findByRole("region", { name: "Notes" }))
      const field = (await notesSection.findByRole("textbox", { name: /Notes/ })) as HTMLTextAreaElement
      expect(field.value).toBe("Great call.")

      fireEvent.change(field, { target: { value: "Great call. Offer prepared." } })
      expect(notesSection.getByRole("status").textContent).toBe("Saving…")
      await waitFor(() => expect(notesSection.getByRole("status").textContent).toBe("Saved"), SAVE_WAIT)
      const patch = api.callsTo("PATCH", /^\/applications\//)
      expect(patch).toHaveLength(1)
      expect(patch[0].path).toBe("/applications/app-new")
      expect(patch[0].body).toEqual({ notes: "Great call. Offer prepared." })
    },
    AUTOSAVE_TEST_TIMEOUT_MS,
  )
})

describe("PersonProfile: states", () => {
  it("shows a skeleton, then not found for an unknown person", async () => {
    installPeopleApi({ people: [], applications: [] })
    renderProfile()
    expect(screen.getByLabelText("Loading person")).toBeTruthy()
    expect(await screen.findByRole("heading", { name: "Person not found" })).toBeTruthy()
    expect(screen.getByRole("link", { name: /Talent/ }).getAttribute("href")).toBe("/talent")
  })

  it("shows the problem and retries", async () => {
    let failures = 1
    installPeopleApi({
      people: [person],
      applications: [newApplication, oldApplication],
      override: ({ path }) =>
        path === `/people/${PERSON_ID}` && failures-- > 0 ? problem(400, "Bad Request", "Try again later") : undefined,
    })
    renderProfile()
    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("Try again later")
    fireEvent.click(within(alert).getByRole("button", { name: /Try again/ }))
    expect(await screen.findByRole("heading", { level: 1, name: "Ada Lovelace" })).toBeTruthy()
  })

  it("says so when the selected application was deleted", async () => {
    installPeopleApi({
      people: [person],
      applications: [oldApplication],
      override: ({ path }) => (path === "/applications/app-new" ? problem(404, "Not Found") : undefined),
    })
    renderProfile()
    expect(await within(await screen.findByRole("region", { name: "Evaluation" })).findByText("Application not found")).toBeTruthy()
    // The rest of the profile still works.
    expect(within(section("Applications")).getAllByRole("row")).toHaveLength(3)
  })
})
