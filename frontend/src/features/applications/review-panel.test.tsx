import { act, fireEvent, screen, waitFor, within } from "@testing-library/react"
import type { UrlUpdateEvent } from "nuqs/adapters/testing"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { clearUnsavedDrafts } from "@/lib/use-autosave"
import { makePerson, makePersonApplication } from "@/test/people"

import { CandidatesTab } from "./candidates-tab"
import { makeApplication } from "./fixtures"
import type { Application } from "./model"
import {
  CAMPAIGN_ID,
  deferred,
  installDomPolyfills,
  installFakeApi,
  json,
  problem,
  renderWithProviders,
} from "./test-utils"

const { toast } = vi.hoisted(() => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}))
vi.mock("sonner", () => ({ toast }))

beforeEach(() => {
  installDomPolyfills()
  toast.success.mockClear()
  toast.warning.mockClear()
  toast.error.mockClear()
})
afterEach(() => {
  clearUnsavedDrafts()
})

// Autosave waits 800 ms after the last key press, so these tests run on real
// timers with waits longer than that, and get their own timeout.
const AUTOSAVE_TEST_TIMEOUT_MS = 15_000
const SAVE_WAIT = { timeout: 4_000 }

const ADA_PERSON_ID = "aaaaaaaa-0000-4000-8000-000000000001"
const OTHER_CAMPAIGN_ID = "22222222-2222-4222-8222-222222222222"

const ada = makeApplication(0, {
  campaign_id: CAMPAIGN_ID,
  person: { id: ADA_PERSON_ID, name: "Ada Lovelace", email: "ada@example.com" },
  score: 82,
  category: "strong_match",
  score_breakdown: {
    skills_match: 32,
    experience_match: 25,
    project_relevance: 12,
    education_bonus: 8,
    communication_quality: 5,
  } as unknown as Application["score_breakdown"],
  summary: "Ada builds analytical engines.",
  recommendation: "Proceed to technical interview",
  strengths: ["Deep React knowledge", "Ships tested code"],
  weaknesses: ["No Kubernetes"],
  missing_skills: ["Kubernetes"],
  skills: ["React", "TypeScript", "PostgreSQL"],
  years_of_experience: 6,
  pipeline_stage: "screened",
  notes: "Called on Monday.",
})
const grace = makeApplication(1, {
  campaign_id: CAMPAIGN_ID,
  person: { id: "p-grace", name: "Grace Hopper", email: "grace@example.com" },
  score: 70,
  category: "moderate_match",
  pipeline_stage: null,
  notes: null,
})
const adaPerson = makePerson({
  id: ADA_PERSON_ID,
  applications: [
    makePersonApplication("app-elsewhere", { id: OTHER_CAMPAIGN_ID, title: "Data platform lead" }, {
      score: 64,
      category: "moderate_match",
      pipeline_stage: "technical",
    }),
    makePersonApplication(ada.id, { id: CAMPAIGN_ID, title: "Senior frontend engineer" }),
  ],
})

function renderTab(searchParams = `?open=${ada.id}`) {
  const onUrlUpdate = vi.fn<(event: UrlUpdateEvent) => void>()
  const result = renderWithProviders(<CandidatesTab campaignId={CAMPAIGN_ID} />, { searchParams, onUrlUpdate })
  return { ...result, onUrlUpdate }
}

const panel = () => screen.getByRole("dialog")
const table = () => document.querySelector<HTMLElement>("[data-slot='candidates-table']")!
const stageOf = (name: string) =>
  within(table()).getByText(name).closest("tr")?.querySelector("td[data-column='stage']")?.textContent
const notesField = () => within(panel()).getByRole("textbox", { name: "Notes" })
const notesStatus = () => within(panel()).getByRole("status")

async function openPanel(searchParams?: string) {
  renderTab(searchParams)
  await within(await screen.findByRole("dialog")).findByRole("heading", { name: "Ada Lovelace" })
}

/** Pick a stage from the panel's selector (Radix Select, opened from the keyboard). */
async function chooseStage(label: string) {
  const trigger = within(panel()).getByRole("combobox", { name: "Stage" })
  fireEvent.keyDown(trigger, { key: "Enter" })
  const listbox = await screen.findByRole("listbox")
  fireEvent.click(within(listbox).getByRole("option", { name: label }))
}

describe("ReviewPanel: content", () => {
  it("shows contact, breakdown, recommendation, strengths, gaps and skills", async () => {
    installFakeApi({ applications: [ada, grace], people: [adaPerson] })
    await openPanel()
    const view = within(panel())

    expect(view.getByRole("link", { name: /ada@example.com/ }).getAttribute("href")).toBe("mailto:ada@example.com")
    expect(view.getByRole("link", { name: /Open full profile/ }).getAttribute("href")).toBe(
      `/people/${ADA_PERSON_ID}?application=${ada.id}`,
    )

    const breakdown = view.getByRole("region", { name: "Score breakdown" })
    const parts = breakdown.querySelectorAll("[data-slot='score-part']")
    expect([...parts].map((part) => part.textContent)).toEqual([
      "Skills match32/40",
      "Experience match25/30",
      "Project relevance12/15",
      "Education8/10",
      "Resume quality5/5",
    ])
    // Bars are as wide as the share of the part's maximum (a CSS variable, no other inline style).
    const bar = parts[0].querySelector<HTMLElement>("[data-slot='score-bar']")
    expect(bar?.style.getPropertyValue("--bar")).toBe("80%")

    expect(view.getByRole("region", { name: "Recommendation" }).textContent).toContain("Proceed to technical interview")
    expect(view.getByRole("region", { name: "Summary" }).textContent).toContain("analytical engines")
    expect(within(view.getByRole("region", { name: "Strengths" })).getAllByRole("listitem")).toHaveLength(2)
    expect(view.getByRole("region", { name: "Weaknesses" }).textContent).toContain("No Kubernetes")
    expect(within(view.getByRole("region", { name: "Missing skills" })).getByText("Kubernetes")).toBeTruthy()
    // The campaign's required skills are highlighted.
    const skills = within(view.getByRole("region", { name: "Skills" }))
    expect(skills.getByText("React").closest("li")?.getAttribute("data-required")).toBe("true")
    expect(skills.getByText("TypeScript").closest("li")?.getAttribute("data-required")).toBeNull()
    expect(view.getByText("6 yrs")).toBeTruthy()
    expect((notesField() as HTMLTextAreaElement).value).toBe("Called on Monday.")
  })

  it("lists the Person's other applications, linking into their campaigns", async () => {
    installFakeApi({ applications: [ada, grace], people: [adaPerson] })
    await openPanel()
    const also = within(panel()).getByRole("region", { name: "Also applied to" })
    const link = await within(also).findByRole("link", { name: "Data platform lead" })
    expect(link.getAttribute("href")).toBe(`/campaigns/${OTHER_CAMPAIGN_ID}?open=app-elsewhere`)
    expect(also.textContent).toContain("Technical")
    // This application is not listed as "another" one.
    expect(within(also).queryByRole("link", { name: "Senior frontend engineer" })).toBeNull()
  })

  it("says when the other applications could not be loaded", async () => {
    installFakeApi({ applications: [ada, grace], people: [] })
    await openPanel()
    const also = within(panel()).getByRole("region", { name: "Also applied to" })
    expect(await within(also).findByText("Could not load their other applications.")).toBeTruthy()
    expect(within(also).getByRole("button", { name: "Retry" })).toBeTruthy()
  })

  it("explains an unscored application instead of an empty breakdown", async () => {
    const pending = { ...ada, score: null, category: null, score_breakdown: null, summary: null, recommendation: null }
    installFakeApi({ applications: [pending], people: [makePerson({ id: ADA_PERSON_ID })] })
    await openPanel()
    expect(within(panel()).getByText(/Not scored yet/)).toBeTruthy()
    expect(within(panel()).queryByRole("region", { name: "Score breakdown" })).toBeNull()
    expect(await within(panel()).findByText("No other applications.")).toBeTruthy()
  })

  it("shows not found for an unknown or another campaign's application, and errors with a retry", async () => {
    const foreign = { ...ada, id: "foreign-app", campaign_id: OTHER_CAMPAIGN_ID }
    let failures = 1
    installFakeApi({
      applications: [grace],
      override: ({ method, path }) => {
        if (method === "GET" && path === "/applications/foreign-app") return json(foreign)
        if (method === "GET" && path === "/applications/flaky" && failures-- > 0) {
          return problem(400, "Bad Request", "The server is having a moment")
        }
        if (method === "GET" && path === "/applications/flaky") return json({ ...ada, id: "flaky" })
        return undefined
      },
    })
    const first = renderTab("?open=missing-app")
    expect(await within(await screen.findByRole("dialog")).findByText("Candidate not found")).toBeTruthy()
    first.unmount()

    const second = renderTab("?open=foreign-app")
    expect(await within(await screen.findByRole("dialog")).findByText("Candidate not found")).toBeTruthy()
    second.unmount()

    renderTab("?open=flaky")
    const dialog = await screen.findByRole("dialog")
    expect(await within(dialog).findByText(/Could not load the candidate/)).toBeTruthy()
    fireEvent.click(within(dialog).getByRole("button", { name: "Retry" }))
    expect(await within(dialog).findByRole("heading", { name: "Ada Lovelace" })).toBeTruthy()
  })
})

describe("ReviewPanel: stage", () => {
  it("moves the candidate optimistically: the panel and the table row change at once", async () => {
    const response = deferred<Response>()
    const api = installFakeApi({
      applications: [ada, grace],
      people: [adaPerson],
      override: ({ method }) => (method === "PATCH" ? response.promise : undefined),
    })
    await openPanel()
    expect(stageOf("Ada Lovelace")).toBe("Screened")

    await chooseStage("Offer")
    await waitFor(() => expect(stageOf("Ada Lovelace")).toBe("Offer"))
    expect(within(panel()).getByRole("combobox", { name: "Stage" }).textContent).toContain("Offer")
    const patch = api.callsTo("PATCH", /^\/applications\//)
    expect(patch).toHaveLength(1)
    expect(patch[0].path).toBe(`/applications/${ada.id}`)
    expect(patch[0].body).toEqual({ pipeline_stage: "offer" })

    api.store[0] = { ...api.store[0], pipeline_stage: "offer" }
    response.resolve(json(api.store[0]))
    await waitFor(() => expect(api.callsTo("GET", /^\/campaigns\/[^/]+\/applications$/).length).toBeGreaterThan(1))
    expect(stageOf("Ada Lovelace")).toBe("Offer")
    expect(toast.error).not.toHaveBeenCalled()
  })

  it("can move a candidate back to unreviewed", async () => {
    const api = installFakeApi({ applications: [ada, grace], people: [adaPerson] })
    await openPanel()
    await chooseStage("Unreviewed")
    await waitFor(() => expect(stageOf("Ada Lovelace")).toBe("Unreviewed"))
    expect(api.callsTo("PATCH", /^\/applications\//)[0].body).toEqual({ pipeline_stage: null })
  })

  it("rolls the panel and the row back when the change fails", async () => {
    const response = deferred<Response>()
    // Refetches after the failure never answer: the old stage must come from the rollback.
    const never = new Promise<Response>(() => {})
    let failed = false
    installFakeApi({
      applications: [ada, grace],
      people: [adaPerson],
      override: ({ method }) => {
        if (method === "PATCH") {
          failed = true
          return response.promise
        }
        return failed ? never : undefined
      },
    })
    await openPanel()
    await chooseStage("Hired")
    await waitFor(() => expect(stageOf("Ada Lovelace")).toBe("Hired"))

    response.resolve(problem(409, "Conflict", "The application changed meanwhile"))
    await waitFor(() => expect(stageOf("Ada Lovelace")).toBe("Screened"))
    expect(within(panel()).getByRole("combobox", { name: "Stage" }).textContent).toContain("Screened")
    expect(toast.error).toHaveBeenCalledWith("Conflict", expect.objectContaining({ description: "The application changed meanwhile" }))
  })

  it("focuses the stage selector with S, but not while typing", async () => {
    installFakeApi({ applications: [ada, grace], people: [adaPerson] })
    await openPanel()
    const trigger = within(panel()).getByRole("combobox", { name: "Stage" })

    fireEvent.keyDown(notesField(), { key: "s" })
    expect(document.activeElement).not.toBe(trigger)
    fireEvent.keyDown(document.body, { key: "s", metaKey: true })
    expect(document.activeElement).not.toBe(trigger)

    fireEvent.keyDown(document.body, { key: "s" })
    expect(document.activeElement).toBe(trigger)
    // A second S does not pick "Screened" by typeahead: letters on the closed selector are ignored.
    fireEvent.keyDown(trigger, { key: "s" })
    expect(screen.queryByRole("listbox")).toBeNull()
  })
})

describe("ReviewPanel: notes autosave", () => {
  it(
    "shows Saving… while typing, saves after a pause and shows Saved",
    async () => {
      const api = installFakeApi({ applications: [ada, grace], people: [adaPerson] })
      await openPanel()
      expect(notesStatus().textContent).toBe("Saved automatically as you type")

      fireEvent.change(notesField(), { target: { value: "Called on Monday. Strong" } })
      fireEvent.change(notesField(), { target: { value: "Called on Monday. Strong yes." } })
      expect(notesStatus().textContent).toBe("Saving…")
      // Debounced: nothing is sent while typing.
      expect(api.callsTo("PATCH", /^\/applications\//)).toHaveLength(0)

      await waitFor(() => expect(notesStatus().textContent).toBe("Saved"), SAVE_WAIT)
      const patches = api.callsTo("PATCH", /^\/applications\//)
      expect(patches).toHaveLength(1)
      expect(patches[0].body).toEqual({ notes: "Called on Monday. Strong yes." })
    },
    AUTOSAVE_TEST_TIMEOUT_MS,
  )

  it(
    "keeps the text on error, shows the problem, and retries",
    async () => {
      let fail = true
      const api = installFakeApi({
        applications: [ada, grace],
        people: [adaPerson],
        override: ({ method }) => (method === "PATCH" && fail ? problem(503, "Service Unavailable", "Database is down") : undefined),
      })
      await openPanel()
      fireEvent.change(notesField(), { target: { value: "Offer next week" } })

      await waitFor(() => expect(notesStatus().textContent).toBe("Not saved: Database is down"), SAVE_WAIT)
      expect((notesField() as HTMLTextAreaElement).value).toBe("Offer next week")
      expect(notesField().getAttribute("aria-invalid")).toBe("true")

      fail = false
      fireEvent.click(within(panel()).getByRole("button", { name: "Retry" }))
      await waitFor(() => expect(notesStatus().textContent).toBe("Saved"), SAVE_WAIT)
      expect(api.callsTo("PATCH", /^\/applications\//).at(-1)?.body).toEqual({ notes: "Offer next week" })
    },
    AUTOSAVE_TEST_TIMEOUT_MS,
  )

  it(
    "saves pending notes at once when switching candidates, then shows the next one's",
    async () => {
      const api = installFakeApi({ applications: [ada, grace], people: [adaPerson] })
      await openPanel()
      fireEvent.change(notesField(), { target: { value: "Switching away" } })

      fireEvent.click(within(table()).getByText("Grace Hopper"))
      await within(panel()).findByRole("heading", { name: "Grace Hopper" })
      // Flushed on switch, without waiting for the pause.
      await waitFor(() => expect(api.callsTo("PATCH", /^\/applications\//)).toHaveLength(1))
      expect(api.callsTo("PATCH", /^\/applications\//)[0].path).toBe(`/applications/${ada.id}`)
      expect((notesField() as HTMLTextAreaElement).value).toBe("")

      // Back to Ada: the saved notes are there (from the cache the save updated).
      fireEvent.click(within(table()).getByText("Ada Lovelace"))
      await within(panel()).findByRole("heading", { name: "Ada Lovelace" })
      expect((notesField() as HTMLTextAreaElement).value).toBe("Switching away")
    },
    AUTOSAVE_TEST_TIMEOUT_MS,
  )

  it(
    "saves on close; a failed last save toasts and keeps the text for next time",
    async () => {
      let fail = true
      installFakeApi({
        applications: [ada, grace],
        people: [adaPerson],
        override: ({ method }) => (method === "PATCH" && fail ? problem(503, "Service Unavailable") : undefined),
      })
      await openPanel()
      fireEvent.change(notesField(), { target: { value: "Do not lose me" } })
      fireEvent.click(within(panel()).getByRole("button", { name: "Close" }))
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
      await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Notes not saved", expect.anything()))

      // Reopened: the kept text is back and saved again.
      fail = false
      act(() => {
        fireEvent.click(within(table()).getByText("Ada Lovelace"))
      })
      await within(await screen.findByRole("dialog")).findByRole("heading", { name: "Ada Lovelace" })
      expect((notesField() as HTMLTextAreaElement).value).toBe("Do not lose me")
      await waitFor(() => expect(notesStatus().textContent).toBe("Saved"), SAVE_WAIT)
    },
    AUTOSAVE_TEST_TIMEOUT_MS,
  )
})
