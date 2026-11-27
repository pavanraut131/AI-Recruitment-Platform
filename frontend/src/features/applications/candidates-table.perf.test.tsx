import { QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen, within } from "@testing-library/react"
import { Profiler, type ProfilerOnRenderCallback } from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { makeQueryClient } from "@/lib/query/query-client"
import { signInForTest } from "@/test/auth"

import { CandidatesTable } from "./candidates-table"
import { CandidatesFixtureView } from "./fixture-view"
import { makeApplications } from "./fixtures"
import { DEFAULT_SORT } from "./model"
import { installDomPolyfills, problem } from "./test-utils"

/**
 * The 500-row bar (EPIC-01 §8): the table renders 500 rows and stays responsive
 * to J/K and selection. The API caps a page at 100 rows, so this renders the
 * table component over synthetic rows (the dev route's fixture mode).
 *
 * Times come from React's Profiler (`actualDuration`: time spent rendering the
 * table for a commit) and the wall clock around each interaction. jsdom is
 * slower than a browser and has no layout or paint, so the bounds are loose;
 * they catch a regression such as every row re-rendering on each key press.
 * Run with `PERF_LOG=1` to print the numbers.
 */

const ROWS = 500
const PRESSES = 40
// The bounds below add up to well over Vitest's default 5 s timeout: a jsdom
// mount of 500 rows takes 1–4 s depending on how many test files run in
// parallel, so the test gets its own timeout and the bounds stay the gate.
const TEST_TIMEOUT_MS = 30_000

type Commit = { phase: string; ms: number }

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b)
  return sorted[Math.floor(sorted.length / 2)]
}

function log(label: string, ms: number) {
  if (process.env.PERF_LOG) console.info(`[perf] ${label}: ${ms.toFixed(1)} ms`)
}

beforeEach(() => {
  installDomPolyfills()
  // The review panel asks for the Person's other applications; the fixture rows have none.
  vi.stubGlobal("fetch", vi.fn(async () => problem(404, "Not Found")))
})

const noop = () => {}

describe(`candidates table with ${ROWS} rows`, () => {
  it("mounts, moves with J/K and selects all within budget", () => {
    const commits: Commit[] = []
    const onCommit = (phase: string, ms: number) => commits.push({ phase, ms })

    let start = performance.now()
    // Signed in (as an owner): the fixture view's panel has editable notes.
    const queryClient = makeQueryClient()
    signInForTest(queryClient)
    render(
      <QueryClientProvider client={queryClient}>
        <CandidatesFixtureView rowCount={ROWS} onCommit={onCommit} />
      </QueryClientProvider>,
    )
    const mountWall = performance.now() - start
    const mountRender = commits.filter((commit) => commit.phase === "mount").reduce((sum, c) => sum + c.ms, 0)
    expect(document.querySelectorAll("tbody tr")).toHaveLength(ROWS)
    log("mount (wall)", mountWall)
    log("mount (React render)", mountRender)

    // J/K: each press moves the active row; only the two rows involved re-render.
    const pressWall: number[] = []
    const pressRender: number[] = []
    for (let index = 0; index < PRESSES; index++) {
      commits.length = 0
      start = performance.now()
      act(() => {
        fireEvent.keyDown(document.activeElement ?? document.body, { key: index < PRESSES - 5 ? "j" : "k" })
      })
      pressWall.push(performance.now() - start)
      pressRender.push(commits.reduce((sum, c) => sum + c.ms, 0))
    }
    expect(document.querySelector("tr[data-active]")?.id).toBe(document.activeElement?.id)
    log("J/K press, median (wall)", median(pressWall))
    log("J/K press, median (React render)", median(pressRender))

    // Select all: every row changes, the worst case for an interaction.
    commits.length = 0
    start = performance.now()
    act(() => {
      fireEvent.click(screen.getByRole("checkbox", { name: "Select all candidates on this page" }))
    })
    const selectWall = performance.now() - start
    const selectRender = commits.reduce((sum, c) => sum + c.ms, 0)
    expect(document.querySelectorAll("tbody tr[data-state='selected']")).toHaveLength(ROWS)
    log("select all (wall)", selectWall)
    log("select all (React render)", selectRender)

    // Typing notes in the open review panel: the draft lives in the notes
    // field, so the table does not render at all (EPIC-01 §8, E1-07).
    act(() => {
      fireEvent.keyDown(document.activeElement!, { key: "Enter" })
    })
    const notes = within(screen.getByRole("dialog")).getByRole("textbox", { name: "Notes" })
    commits.length = 0
    const typeWall: number[] = []
    let text = ""
    for (const letter of "Strong candidate, call back") {
      text += letter
      start = performance.now()
      act(() => {
        fireEvent.change(notes, { target: { value: text } })
      })
      typeWall.push(performance.now() - start)
    }
    const tableCommitsWhileTyping = commits.length
    log("notes key press, median (wall)", median(typeWall))
    if (process.env.PERF_LOG) console.info(`[perf] table commits while typing notes: ${tableCommitsWhileTyping}`)

    // Loose bounds for jsdom on a CI runner (see the note above).
    expect(mountWall).toBeLessThan(10_000)
    expect(median(pressWall)).toBeLessThan(100)
    expect(selectWall).toBeLessThan(5_000)
    expect(tableCommitsWhileTyping).toBe(0)
    expect(median(typeWall)).toBeLessThan(100)
  }, TEST_TIMEOUT_MS)

  it("re-renders only the changed row when one application changes", () => {
    // A stage change or a saved note patches one application in the cached
    // page: TanStack Table rebuilds every Row, but rows compare their
    // application, so only that row renders again.
    const rows = makeApplications(ROWS)
    const commits: number[] = []
    const onRender: ProfilerOnRenderCallback = (_id, _phase, actualDuration) => commits.push(actualDuration)
    const requiredSkills = new Set(["react"])
    const stable = (data: typeof rows) => (
      <Profiler id="candidates-table" onRender={onRender}>
        <CandidatesTable
          rows={data}
          requiredSkills={requiredSkills}
          sort={DEFAULT_SORT}
          onSortChange={noop}
          rowSelection={{}}
          onRowSelectionChange={noop}
          activeId={null}
          onActiveChange={noop}
          onOpen={noop}
        />
      </Profiler>
    )
    const { rerender } = render(stable(rows))
    const mountRender = commits.reduce((sum, ms) => sum + ms, 0)

    const patched = rows.map((row, index) => (index === 7 ? { ...row, pipeline_stage: "offer" as const } : row))
    commits.length = 0
    const start = performance.now()
    rerender(stable(patched))
    const patchWall = performance.now() - start
    const patchRender = commits.reduce((sum, ms) => sum + ms, 0)
    log("one-row update (wall)", patchWall)
    log("one-row update (React render)", patchRender)
    expect(document.querySelectorAll("tbody tr")[7].querySelector("td[data-column='stage']")?.textContent).toBe("Offer")
    // Re-rendering every row costs about as much as the mount; one row is a small fraction of it.
    expect(patchRender).toBeLessThan(mountRender / 5)
  }, TEST_TIMEOUT_MS)
})
