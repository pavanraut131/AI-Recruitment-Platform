import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { campaignFixture, deferred, json, mockApi, problem, renderWithProviders } from "../test-utils"
import { NewCampaignForm } from "./new-campaign-form"

const { push } = vi.hoisted(() => ({ push: vi.fn() }))
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

beforeEach(() => {
  push.mockClear()
})

function renderForm() {
  return renderWithProviders(<NewCampaignForm />)
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
    expect(fetchMock).not.toHaveBeenCalled()
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
      expect(fetchMock).not.toHaveBeenCalled()
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

      const request = fetchMock.mock.calls[0][0]
      expect(await request.json()).toEqual({ job_description: jd })
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
    fireEvent.change(titleInput(), { target: { value: "  Backend hire  " } })
    fireEvent.change(roleInput(), { target: { value: "Backend Engineer" } })
    addSkills("Python")

    fireEvent.click(screen.getByRole("button", { name: "Create campaign" }))

    await waitFor(() => expect(push).toHaveBeenCalledWith(`/campaigns/${created.id}`))
    const request = fetchMock.mock.calls[0][0]
    expect(await request.json()).toEqual({
      title: "Backend hire",
      role: "Backend Engineer",
      description: null,
      required_skills: ["Python"],
      status: "active",
    })
  })
})
