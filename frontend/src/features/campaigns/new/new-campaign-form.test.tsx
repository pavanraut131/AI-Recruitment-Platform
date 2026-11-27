import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { meAs } from "@/test/auth"
import { installDomPolyfills } from "@/test/helpers"

import {
  campaignFixture,
  clientFixture,
  deferred,
  GENERAL_CLIENT,
  json,
  mockApi,
  pageOf,
  problem,
  renderWithProviders,
} from "../test-utils"
import { NewCampaignForm } from "./new-campaign-form"

const { push } = vi.hoisted(() => ({ push: vi.fn() }))
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

beforeEach(() => {
  push.mockClear()
  installDomPolyfills()
  window.localStorage.clear()
})

function renderForm({ searchParams, me }: { searchParams?: string; me?: ReturnType<typeof meAs> } = {}) {
  return renderWithProviders(<NewCampaignForm />, { searchParams, me })
}

const clientSelect = () => screen.getByRole("combobox", { name: "Client" })

/** The agency's one client is preselected once the clients have loaded. */
async function clientPreselected(name = GENERAL_CLIENT.name) {
  await waitFor(() => expect(clientSelect().textContent).toBe(name))
}

/** The requests that change something (the client list's `GET /clients` aside). */
function writes(fetchMock: ReturnType<typeof mockApi>) {
  return fetchMock.mock.calls.map(([request]) => request).filter((request) => request.method !== "GET")
}

const titleInput = () => screen.getByRole("textbox", { name: "Title" })
const roleInput = () => screen.getByRole("textbox", { name: "Role" })
const skillsInput = () => screen.getByRole("textbox", { name: "Required skills" })
const jdInput = () => screen.getByRole("textbox", { name: "Job description" })
const skillChips = () =>
  within(screen.getByRole("list", { name: "Required skills" }))
    .queryAllByRole("listitem")
    .map((item) => item.textContent)

function addSkills(text: string) {
  fireEvent.change(skillsInput(), { target: { value: text } })
  fireEvent.keyDown(skillsInput(), { key: "Enter" })
}

describe("NewCampaignForm", () => {
  it("validates the required fields without calling the API", async () => {
    const fetchMock = mockApi({})
    renderForm()

    fireEvent.click(screen.getByRole("button", { name: "Create campaign" }))

    expect(await screen.findByText("Give the campaign a title.")).toBeTruthy()
    expect(screen.getByText("Enter the role you are hiring for.")).toBeTruthy()
    expect(titleInput().getAttribute("aria-invalid")).toBe("true")
    expect(writes(fetchMock)).toEqual([])
  })

  it("adds skills as chips (comma-separated, without duplicates) and removes them", () => {
    mockApi({})
    renderForm()

    addSkills("React, TypeScript")
    addSkills("react")
    expect(skillChips()).toEqual(["React", "TypeScript"])

    fireEvent.click(screen.getByRole("button", { name: "Remove React" }))
    expect(skillChips()).toEqual(["TypeScript"])

    // Backspace in the empty input removes the last chip.
    fireEvent.keyDown(skillsInput(), { key: "Backspace" })
    expect(skillChips()).toEqual([])
  })

  describe("Extract with AI", () => {
    it("asks for a job description first", () => {
      const fetchMock = mockApi({})
      renderForm()

      fireEvent.click(screen.getByRole("button", { name: "Extract with AI" }))

      expect(screen.getByRole("alert").textContent).toContain("Paste a job description first.")
      expect(writes(fetchMock)).toEqual([])
    })

    it("shows a loading state, then fills the role, skills and an empty description", async () => {
      const response = deferred<Response>()
      const fetchMock = mockApi({ "POST /campaigns/parse-jd": () => response.promise })
      renderForm()
      addSkills("Python")

      const jd = "We are hiring a senior backend engineer with Python and FastAPI."
      fireEvent.change(jdInput(), { target: { value: jd } })
      fireEvent.click(screen.getByRole("button", { name: "Extract with AI" }))

      const pending = await screen.findByRole("button", { name: "Extracting…" })
      expect(pending.hasAttribute("disabled")).toBe(true)
      expect(screen.getByText("Reading the job description…")).toBeTruthy()

      response.resolve(json({ role_title: "Senior Backend Engineer", seniority: "senior", skills: ["python", "FastAPI", "Docker"] }))

      await waitFor(() => expect(roleInput()).toHaveProperty("value", "Senior Backend Engineer"))
      expect(skillChips()).toEqual(["Python", "FastAPI", "Docker"])
      expect(screen.getByRole("textbox", { name: /Description/ })).toHaveProperty("value", jd)
      expect(screen.getByText(/Filled in the role, 2 skills and the description\. Seniority: senior\./)).toBeTruthy()
      expect(screen.getByRole("button", { name: "Extract with AI" }).hasAttribute("disabled")).toBe(false)

      expect(await writes(fetchMock)[0].json()).toEqual({ job_description: jd })
    })

    it("shows the API's problem detail when extraction fails, and leaves the form as it was", async () => {
      mockApi({
        "POST /campaigns/parse-jd": () => problem(502, "Bad Gateway", "The AI provider is not configured."),
      })
      renderForm()
      fireEvent.change(roleInput(), { target: { value: "Engineer" } })

      fireEvent.change(jdInput(), { target: { value: "Some job description" } })
      fireEvent.click(screen.getByRole("button", { name: "Extract with AI" }))

      const alert = await screen.findByRole("alert")
      expect(alert.textContent).toContain("Bad Gateway: The AI provider is not configured.")
      expect(roleInput()).toHaveProperty("value", "Engineer")
      expect(skillChips()).toEqual([])
    })
  })

  it("maps a 422's field errors onto the form fields", async () => {
    mockApi({
      "POST /campaigns": () =>
        problem(422, "Unprocessable Entity", "2 invalid fields", [
          { loc: ["body", "title"], msg: "Title already used" },
          { loc: ["body", "required_skills", 1], msg: "String should have at most 100 characters" },
        ]),
    })
    renderForm()
    await clientPreselected()
    fireEvent.change(titleInput(), { target: { value: "Backend hire" } })
    fireEvent.change(roleInput(), { target: { value: "Backend Engineer" } })
    addSkills("Python, Go")

    fireEvent.click(screen.getByRole("button", { name: "Create campaign" }))

    expect(await screen.findByText("Title already used")).toBeTruthy()
    expect(screen.getByText("Skill 2: String should have at most 100 characters")).toBeTruthy()
    expect(titleInput().getAttribute("aria-invalid")).toBe("true")
    expect(push).not.toHaveBeenCalled()
  })

  it("shows other failures above the buttons", async () => {
    mockApi({ "POST /campaigns": () => problem(500, "Internal Server Error", "Something broke.") })
    renderForm()
    await clientPreselected()
    fireEvent.change(titleInput(), { target: { value: "Backend hire" } })
    fireEvent.change(roleInput(), { target: { value: "Backend Engineer" } })

    fireEvent.click(screen.getByRole("button", { name: "Create campaign" }))

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toBe("Internal Server Error: Something broke.")
  })

  it("creates the campaign and opens its workspace", async () => {
    const created = campaignFixture({ id: "22222222-2222-4222-8222-222222222222" })
    const fetchMock = mockApi({ "POST /campaigns": () => json(created, 201) })
    renderForm()
    await clientPreselected()
    fireEvent.change(titleInput(), { target: { value: "  Backend hire  " } })
    fireEvent.change(roleInput(), { target: { value: "Backend Engineer" } })
    addSkills("Python")

    fireEvent.click(screen.getByRole("button", { name: "Create campaign" }))

    await waitFor(() => expect(push).toHaveBeenCalledWith(`/campaigns/${created.id}`))
    expect(await writes(fetchMock)[0].json()).toEqual({
      client_id: GENERAL_CLIENT.id,
      title: "Backend hire",
      role: "Backend Engineer",
      description: null,
      required_skills: ["Python"],
      status: "active",
    })
  })
})

describe("NewCampaignForm: the client (E2-10)", () => {
  const ACME = clientFixture({ id: "00000000-0000-4000-8000-00000000c002", name: "Acme Corp" })
  const OLD = clientFixture({ id: "00000000-0000-4000-8000-00000000c003", name: "Old Co", status: "archived" })

  function fillIn() {
    fireEvent.change(titleInput(), { target: { value: "Backend hire" } })
    fireEvent.change(roleInput(), { target: { value: "Backend Engineer" } })
  }

  it("requires a client when there are several, offering only the active ones", async () => {
    const fetchMock = mockApi({ "GET /clients": () => json(pageOf([ACME, GENERAL_CLIENT, OLD])) })
    renderForm()
    await waitFor(() => expect(clientSelect().textContent).toBe("Choose a client"))
    fillIn()

    fireEvent.click(screen.getByRole("button", { name: "Create campaign" }))

    expect(await screen.findByText("Choose the client this campaign is for.")).toBeTruthy()
    expect(writes(fetchMock)).toEqual([])
    fireEvent.click(clientSelect())
    expect((await screen.findAllByRole("option")).map((option) => option.textContent)).toEqual(["Acme Corp", "General"])
  })

  it("starts with the client from the URL (the list's or Home's) and sends it", async () => {
    const created = campaignFixture({ id: "22222222-2222-4222-8222-222222222222" })
    const fetchMock = mockApi({
      "GET /clients": () => json(pageOf([ACME, GENERAL_CLIENT])),
      "POST /campaigns": () => json(created, 201),
    })
    renderForm({ searchParams: `?client=${ACME.id}` })
    await clientPreselected("Acme Corp")
    fillIn()

    fireEvent.click(screen.getByRole("button", { name: "Create campaign" }))

    await waitFor(() => expect(push).toHaveBeenCalledWith(`/campaigns/${created.id}`))
    expect((await writes(fetchMock)[0].json()).client_id).toBe(ACME.id)
  })

  it("lets an admin add a client inline, then selects it", async () => {
    const added = clientFixture({ id: "00000000-0000-4000-8000-00000000c004", name: "Finch Pay" })
    const clients = [ACME, GENERAL_CLIENT]
    const fetchMock = mockApi({
      "GET /clients": () => json(pageOf(clients)),
      "POST /clients": () => {
        clients.push(added)
        return json(added, 201)
      },
    })
    renderForm({ me: meAs("admin") })
    await waitFor(() => expect(clientSelect().textContent).toBe("Choose a client"))

    fireEvent.click(screen.getByRole("button", { name: "New client" }))
    const dialog = await screen.findByRole("dialog", { name: "New client" })
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Name" }), { target: { value: "  Finch Pay " } })
    fireEvent.click(within(dialog).getByRole("button", { name: "Add client" }))

    await waitFor(() => expect(clientSelect().textContent).toBe("Finch Pay"))
    expect(screen.queryByRole("dialog")).toBeNull()
    const requests = writes(fetchMock)
    // Only the client was created: the dialog's submit did not submit the campaign form around it.
    expect(requests.map((request) => new URL(request.url).pathname)).toEqual(["/api/v2/clients"])
    expect(await requests[0].json()).toEqual({ name: "Finch Pay" })
  })

  it("shows a taken client name under the field (409)", async () => {
    mockApi({
      "POST /clients": () => problem(409, "Conflict", "A client named General already exists"),
    })
    renderForm({ me: meAs("owner") })
    fireEvent.click(screen.getByRole("button", { name: "New client" }))
    const dialog = await screen.findByRole("dialog", { name: "New client" })
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Name" }), { target: { value: "general" } })
    fireEvent.click(within(dialog).getByRole("button", { name: "Add client" }))

    expect(await within(dialog).findByText("A client named General already exists")).toBeTruthy()
    expect(within(dialog).getByRole("textbox", { name: "Name" }).getAttribute("aria-invalid")).toBe("true")
  })

  it("offers a recruiter no New client", async () => {
    mockApi({})
    renderForm({ me: meAs("recruiter") })
    await clientPreselected()
    expect(screen.queryByRole("button", { name: "New client" })).toBeNull()
  })

  it("shows a viewer the no-access state instead of the form", () => {
    const fetchMock = mockApi({})
    renderForm({ me: meAs("viewer") })

    expect(screen.getByRole("heading", { name: "You don't have access to this page" })).toBeTruthy()
    expect(screen.getByText(/As a viewer in Northbank Talent, you can't create campaigns\./)).toBeTruthy()
    expect(screen.getByRole("link", { name: "Go to Campaigns" }).getAttribute("href")).toBe("/campaigns")
    expect(screen.queryByRole("form", { name: "New campaign" })).toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
