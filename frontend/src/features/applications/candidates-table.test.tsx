import { fireEvent, render, screen, within } from "@testing-library/react"
import type { RowSelectionState } from "@tanstack/react-table"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { mustHaves } from "@/lib/applications"

import { CandidatesTable, type CandidatesTableProps } from "./candidates-table"
import { makeApplication } from "./fixtures"
import { installDomPolyfills } from "./test-utils"

beforeEach(() => {
  installDomPolyfills()
})

const strong = makeApplication(1, {
  person: { id: "p1", name: "Ada Lovelace", email: "ada@example.com" },
  score: 87.4,
  category: "strong_match",
  skills: ["Python", "react", "Docker", "PostgreSQL", "Go"],
  years_of_experience: 6.5,
  pipeline_stage: "technical",
})
const unscored = makeApplication(2, {
  person: { id: "p2", name: null, email: null },
  score: null,
  category: null,
  skills: [],
  years_of_experience: null,
  pipeline_stage: null,
})

function renderTable(props: Partial<CandidatesTableProps> = {}) {
  const handlers = {
    onSortChange: vi.fn(),
    onRowSelectionChange: vi.fn(),
    onActiveChange: vi.fn(),
    onOpen: vi.fn(),
  }
  render(
    <CandidatesTable
      rows={[strong, unscored]}
      requiredSkills={new Set(["react", "postgresql"])}
      sort="-score"
      rowSelection={{}}
      activeId={null}
      {...handlers}
      {...props}
    />,
  )
  return handlers
}

const rowOf = (text: string) => screen.getByText(text).closest("tr") as HTMLTableRowElement

describe("CandidatesTable columns", () => {
  it("shows the headers in the spec's order", () => {
    renderTable()
    const headers = screen.getAllByRole("columnheader").map((header) => header.textContent)
    expect(headers).toEqual(["", "Candidate", "Score", "Must-haves", "Experience", "Stage", "Updated"])
  })

  it("shows the name with the email under it", () => {
    renderTable()
    const row = rowOf("Ada Lovelace")
    expect(within(row).getByText("ada@example.com")).toBeTruthy()
  })

  it("shows the rounded score, a 5-segment meter in the category colour and the label", () => {
    renderTable()
    const row = rowOf("Ada Lovelace")
    expect(within(row).getByText("87")).toBeTruthy()
    const segments = [...row.querySelectorAll<HTMLElement>("[data-slot='score-meter'] > span")]
    expect(segments).toHaveLength(5)
    // 87 rounds to 4 of 5 segments.
    expect(segments.filter((segment) => segment.className.includes("bg-category-strong"))).toHaveLength(4)
    expect(within(row).getByText("Strong match").className).toContain("text-category-strong-foreground")
  })

  it("counts the campaign's must-haves the candidate has, and shows experience and stage", () => {
    renderTable()
    const row = rowOf("Ada Lovelace")
    const musts = row.querySelector<HTMLElement>("td[data-column='musts'] span")
    expect(musts?.textContent).toBe("2 of 2")
    expect(musts?.title).toBe("Has every must-have")
    expect(within(row).getByText("6.5 yrs")).toBeTruthy()
    expect(within(row).getByText("Technical")).toBeTruthy()
  })

  it("names the missing must-haves", () => {
    renderTable({ requiredSkills: new Set(["react", "kubernetes"]) })
    const musts = rowOf("Ada Lovelace").querySelector<HTMLElement>("td[data-column='musts'] span")
    expect(musts?.textContent).toBe("1 of 2")
    expect(musts?.title).toBe("Missing: kubernetes")
  })

  it("shows dashes and fallbacks for missing values, and no meter", () => {
    renderTable()
    const row = rowOf("Unnamed candidate")
    expect(within(row).getByText("No email")).toBeTruthy()
    expect(within(row).getByLabelText("Not scored")).toBeTruthy()
    expect(row.querySelector("[data-slot='score-meter']")).toBeNull()
    expect(within(row).getByText("New")).toBeTruthy()
    expect(within(row).getByText("0 of 2")).toBeTruthy()
    // score and experience
    expect(within(row).getAllByText("—")).toHaveLength(2)
  })
})

describe("mustHaves", () => {
  it("checks required skills case-insensitively, in the campaign's order, once each", () => {
    expect(mustHaves(["React", "Go", "react", " "], ["REACT", "Rust"])).toEqual([
      { skill: "React", has: true },
      { skill: "Go", has: false },
    ])
  })
})

describe("CandidatesTable sorting", () => {
  it("marks the server sort on its column", () => {
    renderTable({ sort: "-score" })
    expect(screen.getByRole("columnheader", { name: /Score/ }).getAttribute("aria-sort")).toBe("descending")
    expect(screen.getByRole("columnheader", { name: /Candidate/ }).getAttribute("aria-sort")).toBe("none")
    expect(screen.getByRole("columnheader", { name: /Must-haves/ }).getAttribute("aria-sort")).toBeNull()
  })

  it("maps header clicks to the API's sort values", () => {
    const { onSortChange } = renderTable({ sort: "-score" })
    fireEvent.click(screen.getByRole("button", { name: "Sort by score" }))
    expect(onSortChange).toHaveBeenLastCalledWith("score")
    fireEvent.click(screen.getByRole("button", { name: "Sort by last updated" }))
    expect(onSortChange).toHaveBeenLastCalledWith("-updated_at")
    fireEvent.click(screen.getByRole("button", { name: "Sort by candidate name" }))
    expect(onSortChange).toHaveBeenLastCalledWith("name")
  })

  it("shows ascending score", () => {
    renderTable({ sort: "score" })
    expect(screen.getByRole("columnheader", { name: /Score/ }).getAttribute("aria-sort")).toBe("ascending")
  })
})

describe("CandidatesTable rows", () => {
  it("selects rows without opening them", () => {
    const { onRowSelectionChange, onOpen } = renderTable()
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Ada Lovelace" }))
    expect(onOpen).not.toHaveBeenCalled()
    const updater = onRowSelectionChange.mock.lastCall?.[0] as (old: RowSelectionState) => RowSelectionState
    expect(updater({})).toEqual({ [strong.id]: true })
  })

  it("selects every row of the page from the header", () => {
    const { onRowSelectionChange } = renderTable()
    fireEvent.click(screen.getByRole("checkbox", { name: "Select all candidates on this page" }))
    const updater = onRowSelectionChange.mock.lastCall?.[0] as (old: RowSelectionState) => RowSelectionState
    expect(updater({})).toEqual({ [strong.id]: true, [unscored.id]: true })
  })

  it("shows the selection and an indeterminate header", () => {
    renderTable({ rowSelection: { [strong.id]: true } })
    expect(rowOf("Ada Lovelace").getAttribute("data-state")).toBe("selected")
    const all = screen.getByRole("checkbox", { name: "Select all candidates on this page" }) as HTMLInputElement
    expect(all.indeterminate).toBe(true)
    expect(all.checked).toBe(false)
  })

  it("opens a row on click and on Enter", () => {
    const { onOpen, onActiveChange } = renderTable()
    fireEvent.click(screen.getByText("Ada Lovelace"))
    expect(onActiveChange).toHaveBeenCalledWith(strong.id)
    expect(onOpen).toHaveBeenCalledWith(strong.id)
    fireEvent.keyDown(rowOf("Unnamed candidate"), { key: "Enter" })
    expect(onOpen).toHaveBeenLastCalledWith(unscored.id)
  })

  it("makes the active row the one Tab lands on", () => {
    renderTable({ activeId: unscored.id })
    expect(rowOf("Unnamed candidate").tabIndex).toBe(0)
    expect(rowOf("Unnamed candidate").getAttribute("data-active")).toBe("true")
    expect(rowOf("Ada Lovelace").tabIndex).toBe(-1)
  })
})
