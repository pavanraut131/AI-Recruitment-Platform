import { act, fireEvent, screen, waitFor, within } from "@testing-library/react"
import { toast } from "sonner"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { useCampaignEvents } from "@/lib/query/use-campaign-events"

import {
  CAMPAIGN_ID,
  installFakeApi,
  makeFile,
  makeResume,
  MockEventSource,
  MockXHR,
  problem,
  renderWithProviders,
} from "./test-utils"
import { meAs } from "@/test/auth"

import { UploadsTab } from "./uploads-tab"

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }))

// The tests wait for the hook's debounced invalidations (300 ms) and refetches
// in real time.
const TIMING_TIMEOUT_MS = 15_000

/** The tab as the workspace hosts it: the workspace mounts the campaign's event stream. */
function Workspace() {
  useCampaignEvents(CAMPAIGN_ID)
  return <UploadsTab campaignId={CAMPAIGN_ID} />
}

function pickFiles(files: File[]) {
  fireEvent.change(screen.getByLabelText("PDF resumes"), { target: { files } })
}

/** The row of a file, by its name. */
function row(filename: string) {
  return screen.getByText(filename, { selector: "p" }).closest("li") as HTMLElement
}

function stepOf(filename: string) {
  return row(filename).getAttribute("data-step")
}

function liveRegions() {
  return screen.getAllByRole("status").map((region) => region.textContent)
}

beforeEach(() => {
  MockXHR.install()
  MockEventSource.install()
  vi.mocked(toast.success).mockClear()
  vi.mocked(toast.info).mockClear()
  vi.mocked(toast.error).mockClear()
})

describe("UploadsTab", () => {
  it("shows the dropzone and an empty state for a campaign without resumes", async () => {
    installFakeApi()
    renderWithProviders(<Workspace />)

    expect(screen.getByLabelText("Loading resumes")).toBeTruthy()
    expect(await screen.findByText("No resumes yet")).toBeTruthy()
    expect(screen.getByRole("group", { name: "Upload resumes" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "Choose PDFs" })).toBeTruthy()
    expect(screen.getByText(/PDF only, up to 10 MB each/)).toBeTruthy()
    expect(screen.getByLabelText("PDF resumes").getAttribute("accept")).toBe("application/pdf,.pdf")
  })

  it(
    "moves an uploaded file through Uploading → Queued → Reading → Scoring → Done, live from the events",
    async () => {
      const { server, callsTo } = installFakeApi()
      renderWithProviders(<Workspace />)
      await screen.findByText("No resumes yet")
      act(() => MockEventSource.latest().emit("connected", { campaign_id: CAMPAIGN_ID }))

      pickFiles([makeFile("ada.pdf", 2_000)])
      await waitFor(() => expect(MockXHR.instances).toHaveLength(1))
      const request = MockXHR.latest()
      expect(request.files().map((file) => file.name)).toEqual(["ada.pdf"])
      expect(stepOf("ada.pdf")).toBe("uploading")
      expect(liveRegions()).toContain("Uploading 1 file.")

      // Progress: the row's bar and the live region (in 25 % steps).
      act(() => request.progress(600, 1_000))
      const bar = within(row("ada.pdf")).getByRole("progressbar", { name: "Uploading ada.pdf" })
      expect(bar.getAttribute("aria-valuenow")).toBe("60")
      expect(liveRegions()).toContain("Uploading 1 file: 50%.")

      // Accepted: the file is a resume now, queued for processing, at the top of the list.
      const resume = makeResume("r-ada", { filename: "ada.pdf", size_bytes: 2_000 })
      server.resumes.unshift(resume)
      act(() => request.respond(202, { accepted: [resume], rejected: [] }))
      await waitFor(() => expect(stepOf("ada.pdf")).toBe("queued"))
      expect(within(row("ada.pdf")).getByText("Queued")).toBeTruthy()
      expect(screen.queryByText("This upload")).toBeNull()
      expect(liveRegions()).toContain("Uploaded 1 file: all accepted.")
      expect(screen.queryByText("No resumes yet")).toBeNull()

      // The workers move it on; each step arrives as an event, with no polling.
      const listCalls = () => callsTo("GET", /\/resumes$/).length
      await waitFor(() => expect(listCalls()).toBeGreaterThan(1)) // the refetch after the upload
      const before = listCalls()
      server.resumes[0].status = "extracting"
      act(() => MockEventSource.latest().resumeUpdate("r-ada", "extracting", { filename: "ada.pdf" }))
      await waitFor(() => expect(stepOf("ada.pdf")).toBe("extracting"))
      expect(within(row("ada.pdf")).getByText("Reading")).toBeTruthy()

      // (The backend does not publish `processing` today, see the PR's API gaps;
      // the cache patch shows it as soon as it does.)
      server.resumes[0].status = "processing"
      act(() => MockEventSource.latest().resumeUpdate("r-ada", "processing"))
      await waitFor(() => expect(stepOf("ada.pdf")).toBe("analysing"))
      expect(within(row("ada.pdf")).getByText("Scoring")).toBeTruthy()
      expect(listCalls()).toBe(before) // patched in place, not refetched

      Object.assign(server.resumes[0], { status: "done", processing_step: "save", application_id: "app-ada" })
      act(() => MockEventSource.latest().resumeUpdate("r-ada", "done", { score: 88, category: "strong_match" }))
      await waitFor(() => expect(stepOf("ada.pdf")).toBe("done"))
      expect(within(row("ada.pdf")).getByText("Done")).toBeTruthy()
      // The event has no application id; the debounced refetch brings it, and the row links to the candidate.
      const link = await within(row("ada.pdf")).findByRole("link", { name: "View candidate" })
      expect(link.getAttribute("href")).toBe(`/campaigns/${CAMPAIGN_ID}?tab=candidates&open=app-ada`)
    },
    TIMING_TIMEOUT_MS,
  )

  it(
    "shows a failure's step and error, the partial state, and retries the failed resumes",
    async () => {
      const { server, callsTo } = installFakeApi({
        resumes: [makeResume("r-1", { filename: "ok.pdf", status: "done", application_id: "app-1" }), makeResume("r-2", { filename: "bad.pdf", status: "processing" })],
      })
      renderWithProviders(<Workspace />)
      await screen.findByText("bad.pdf")
      expect(screen.queryByTestId("partial-state")).toBeNull()

      Object.assign(server.resumes[1], { status: "failed", processing_step: "extract_profile", error_message: "Not a resume" })
      act(() => MockEventSource.latest().resumeUpdate("r-2", "failed", { error: "Not a resume" }))
      await waitFor(() => expect(stepOf("bad.pdf")).toBe("failed"))
      // The step that failed comes with the refetch the hook triggers.
      expect(await within(row("bad.pdf")).findByText("Failed while extracting the profile")).toBeTruthy()
      expect(within(row("bad.pdf")).getByText(/Not a resume/)).toBeTruthy()

      const partial = await screen.findByTestId("partial-state")
      expect(partial.textContent).toContain("1 resume could not be processed")
      expect(stepOf("ok.pdf")).toBe("done")

      fireEvent.click(within(partial).getByRole("button", { name: "Retry failed" }))
      await waitFor(() => expect(callsTo("POST", /\/resumes\/retry-failed$/)).toHaveLength(1))
      await waitFor(() => expect(stepOf("bad.pdf")).toBe("queued"))
      expect(toast.success).toHaveBeenCalledWith("Retrying 1 resume")
      await waitFor(() => expect(screen.queryByTestId("partial-state")).toBeNull())
    },
    TIMING_TIMEOUT_MS,
  )

  it("says when to retry a quota-limited resume", async () => {
    installFakeApi({
      resumes: [makeResume("r-1", { filename: "late.pdf", status: "quota_exceeded", processing_step: "score", error_message: "Gemini API daily quota reached" })],
    })
    renderWithProviders(<Workspace />)

    await screen.findByText("late.pdf")
    expect(stepOf("late.pdf")).toBe("quota")
    expect(within(row("late.pdf")).getByText("Quota limit")).toBeTruthy()
    expect(row("late.pdf").textContent).toMatch(/daily quota is used up\. Retry after it resets: .+ \(in \d+ (hours?|minutes?|seconds?)\)/)
    expect(row("late.pdf").querySelector("time[datetime$='Z']")).toBeTruthy()
    expect(screen.getByTestId("partial-state").textContent).toContain("1 resume could not be processed")
  })

  it("takes dropped files, and opens the file picker from its button (click, Enter or Space)", async () => {
    installFakeApi()
    renderWithProviders(<Workspace />)
    await screen.findByText("No resumes yet")
    const zone = screen.getByRole("group", { name: "Upload resumes" })

    const files = [makeFile("dropped.pdf")]
    fireEvent.drop(zone, {
      dataTransfer: {
        files,
        items: files.map((file) => ({ kind: "file", type: file.type, getAsFile: () => file })),
        types: ["Files"],
      },
    })
    await waitFor(() => expect(MockXHR.instances).toHaveLength(1))
    expect(MockXHR.latest().files().map((file) => file.name)).toEqual(["dropped.pdf"])

    // The button is the zone's one tab stop; a click (or Enter / Space, which
    // click a button) opens the picker.
    const openPicker = vi.spyOn(HTMLInputElement.prototype, "click").mockImplementation(() => {})
    const button = screen.getByRole("button", { name: "Choose PDFs" })
    expect(zone.hasAttribute("tabindex")).toBe(false)
    fireEvent.click(button)
    expect(openPicker).toHaveBeenCalledTimes(1)
  })

  it("checks type and size on the client, with the server's reasons, and uploads only what passes", async () => {
    installFakeApi()
    renderWithProviders(<Workspace />)
    await screen.findByText("No resumes yet")

    pickFiles([makeFile("notes.docx", 100), makeFile("huge.pdf", 10 * 1024 * 1024 + 1), makeFile("ok.PDF", 100)])
    await waitFor(() => expect(MockXHR.instances).toHaveLength(1))

    expect(MockXHR.latest().files().map((file) => file.name)).toEqual(["ok.PDF"])
    expect(stepOf("notes.docx")).toBe("rejected")
    expect(within(row("notes.docx")).getByText("Only PDF files are accepted")).toBeTruthy()
    expect(within(row("huge.pdf")).getByText("File exceeds 10MB limit")).toBeTruthy()
    expect(liveRegions()).toContain(
      "Uploading 1 file. 2 files not uploaded: notes.docx (Only PDF files are accepted), huge.pdf (File exceeds 10MB limit).",
    )
  })

  it("sends a drop of more than 50 files as batches, the later ones Queued until their turn", async () => {
    installFakeApi()
    renderWithProviders(<Workspace />)
    await screen.findByText("No resumes yet")

    pickFiles(Array.from({ length: 51 }, (_, index) => makeFile(`cv-${index}.pdf`)))
    await waitFor(() => expect(MockXHR.instances).toHaveLength(1))
    expect(MockXHR.latest().files()).toHaveLength(50)
    expect(stepOf("cv-0.pdf")).toBe("uploading")
    expect(stepOf("cv-50.pdf")).toBe("queued")
    expect(liveRegions()).toContain("Uploading 50 files: 0%. 1 file queued.")

    act(() => MockXHR.latest().respond(202, { accepted: [], rejected: [] }))
    await waitFor(() => expect(MockXHR.instances).toHaveLength(2))
    expect(MockXHR.latest().files().map((file) => file.name)).toEqual(["cv-50.pdf"])
    await waitFor(() => expect(stepOf("cv-50.pdf")).toBe("uploading"))
  })

  it("shows the server's rejections (duplicates) inline, and clears them", async () => {
    installFakeApi()
    renderWithProviders(<Workspace />)
    await screen.findByText("No resumes yet")

    pickFiles([makeFile("dup.pdf"), makeFile("fake.pdf")])
    await waitFor(() => expect(MockXHR.instances).toHaveLength(1))
    act(() =>
      MockXHR.latest().respond(202, {
        accepted: [],
        rejected: [
          { filename: "dup.pdf", reason: "Duplicate file (already processed successfully)" },
          { filename: "fake.pdf", reason: "Invalid PDF file" },
        ],
      }),
    )

    await waitFor(() => expect(stepOf("dup.pdf")).toBe("rejected"))
    expect(within(row("dup.pdf")).getByText("Duplicate file (already processed successfully)")).toBeTruthy()
    expect(within(row("fake.pdf")).getByText("Invalid PDF file")).toBeTruthy()
    expect(liveRegions()).toContain("Uploaded 2 files: 0 accepted, 2 rejected.")

    fireEvent.click(screen.getByRole("button", { name: "Clear" }))
    await waitFor(() => expect(screen.queryByText("dup.pdf")).toBeNull())
    expect(screen.queryByText("This upload")).toBeNull()
  })

  it("shows a failed request on its files and sends them again", async () => {
    installFakeApi()
    renderWithProviders(<Workspace />)
    await screen.findByText("No resumes yet")

    pickFiles([makeFile("ada.pdf")])
    await waitFor(() => expect(MockXHR.instances).toHaveLength(1))
    act(() => MockXHR.latest().respond(500, { type: "about:blank", title: "Internal Server Error", status: 500, detail: "Storage is down." }))

    await waitFor(() => expect(stepOf("ada.pdf")).toBe("upload_failed"))
    expect(within(row("ada.pdf")).getByText("Storage is down.")).toBeTruthy()
    expect(toast.error).not.toHaveBeenCalled() // shown on the row instead

    fireEvent.click(within(row("ada.pdf")).getByRole("button", { name: "Upload again" }))
    await waitFor(() => expect(MockXHR.instances).toHaveLength(2))
    expect(MockXHR.latest().files().map((file) => file.name)).toEqual(["ada.pdf"])
    await waitFor(() => expect(stepOf("ada.pdf")).toBe("uploading"))
  })

  it("keeps an upload going while the tab is away (it lives in the mutation cache)", async () => {
    installFakeApi()
    const view = renderWithProviders(<Workspace />)
    await screen.findByText("No resumes yet")

    pickFiles([makeFile("ada.pdf")])
    await waitFor(() => expect(MockXHR.instances).toHaveLength(1))
    act(() => MockXHR.latest().progress(30, 100))

    view.rerenderWith(<p>Candidates tab</p>)
    expect(screen.queryByText("ada.pdf")).toBeNull()
    view.rerenderWith(<Workspace />)

    expect(stepOf("ada.pdf")).toBe("uploading")
    expect(within(row("ada.pdf")).getByRole("progressbar").getAttribute("aria-valuenow")).toBe("30")
    expect(MockXHR.instances).toHaveLength(1)
  })

  it("shows a failed load with its detail, and retries", async () => {
    let fail = true
    installFakeApi({
      resumes: [makeResume("r-1", { filename: "ok.pdf" })],
      override: ({ method, path }) =>
        fail && method === "GET" && path.endsWith("/resumes") ? problem(500, "Internal Server Error", "Database unavailable.") : undefined,
    })
    renderWithProviders(<Workspace />)

    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain("Could not load the resumes: Internal Server Error")
    expect(alert.textContent).toContain("Database unavailable.")

    fail = false
    fireEvent.click(within(alert).getByRole("button", { name: "Try again" }))
    expect(await screen.findByText("ok.pdf")).toBeTruthy()
  })
})

describe("UploadsTab: a viewer (E2-10)", () => {
  it("lists the resumes without the dropzone or Retry failed", async () => {
    installFakeApi({
      resumes: [makeResume("r1", { filename: "ada.pdf", status: "failed", error_message: "Could not read the PDF" })],
    })
    renderWithProviders(<Workspace />, { me: meAs("viewer") })

    expect(await screen.findByText("ada.pdf")).toBeTruthy()
    expect(screen.getByText(/Recruiters, admins and owners upload them\./)).toBeTruthy()
    expect(screen.queryByRole("group", { name: "Upload resumes" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Choose PDFs" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Retry failed" })).toBeNull()
  })
})
