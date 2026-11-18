import { act, fireEvent, render, screen } from "@testing-library/react"
import { beforeEach, describe, expect, it } from "vitest"

import { CandidatesFixtureView } from "./fixture-view"
import { installDomPolyfills } from "./test-utils"

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
})

describe(`candidates table with ${ROWS} rows`, () => {
  it("mounts, moves with J/K and selects all within budget", () => {
    const commits: Commit[] = []
    const onCommit = (phase: string, ms: number) => commits.push({ phase, ms })

    let start = performance.now()
    render(<CandidatesFixtureView rowCount={ROWS} onCommit={onCommit} />)
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

    // Loose bounds for jsdom on a CI runner (see the note above).
    expect(mountWall).toBeLessThan(10_000)
    expect(median(pressWall)).toBeLessThan(100)
    expect(selectWall).toBeLessThan(5_000)
  }, TEST_TIMEOUT_MS)
})
