import { fireEvent, render, screen, within } from "@testing-library/react"
import type { RowSelectionState } from "@tanstack/react-table"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { topSkills } from "./cells"
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
    expect(headers).toEqual(["", "Candidate", "Score", "Category", "Top skills", "Experience", "Stage", "Updated"])
  })

  it("shows the name with the email under it", () => {
    renderTable()
    const row = rowOf("Ada Lovelace")
    expect(within(row).getByText("ada@example.com")).toBeTruthy()
  })

  it("shows the rounded score with a bar in the category colour", () => {
    renderTable()
    const row = rowOf("Ada Lovelace")
    expect(within(row).getByText("87")).toBeTruthy()
    const bar = row.querySelector<HTMLElement>("[data-slot='score-bar']")
    expect(bar?.className).toContain("bg-category-strong")
    expect(bar?.style.getPropertyValue("--bar")).toBe("87.4%")
    expect(within(row).getByText("Strong match").getAttribute("data-variant")).toBe("strong")
  })

  it("puts the campaign's required skills first, highlighted, and counts the rest", () => {
    renderTable()
    const row = rowOf("Ada Lovelace")
    const skills = row.querySelectorAll<HTMLElement>("td[data-column='skills'] span[title]")
    expect([...skills].map((skill) => skill.title)).toEqual([
      "react (required skill)",
      "PostgreSQL (required skill)",
      "Python",
    ])
    expect(skills[0].dataset.required).toBe("true")
    expect(skills[2].dataset.required).toBeUndefined()
    expect(within(row).getByText("+2")).toBeTruthy()
    expect(within(row).getByText("6.5 yrs")).toBeTruthy()
    expect(within(row).getByText("Technical")).toBeTruthy()
  })

  it("shows dashes and fallbacks for missing values, and no score bar", () => {
    renderTable()
    const row = rowOf("Unnamed candidate")
    expect(within(row).getByText("No email")).toBeTruthy()
    expect(within(row).getByLabelText("Not scored")).toBeTruthy()
    expect(row.querySelector("[data-slot='score-bar']")).toBeNull()
    expect(within(row).getByText("Unreviewed")).toBeTruthy()
    // category, skills and experience
    expect(within(row).getAllByText("—")).toHaveLength(4)
  })
})

describe("topSkills", () => {
  it("matches required skills case-insensitively and drops duplicates", () => {
    expect(topSkills(["Go", "go", "REACT", "Rust"], new Set(["react"]))).toEqual({
      shown: [
        { skill: "REACT", required: true },
        { skill: "go", required: false },
        { skill: "Rust", required: false },
      ],
      more: 0,
    })
  })
})

describe("CandidatesTable sorting", () => {
  it("marks the server sort on its column", () => {
    renderTable({ sort: "-score" })
    expect(screen.getByRole("columnheader", { name: /Score/ }).getAttribute("aria-sort")).toBe("descending")
    expect(screen.getByRole("columnheader", { name: /Candidate/ }).getAttribute("aria-sort")).toBe("none")
    expect(screen.getByRole("columnheader", { name: /Category/ }).getAttribute("aria-sort")).toBeNull()
  })

  it("maps header clicks to the API's sort values", () => {
    const { onSortChange } = renderTable({ sort: "-score" })
    fireEvent.click(screen.getByRole("button", { name: "Sort by score" }))
    expect(onSortChange).toHaveBeenLastCalledWith("score")
    fireEvent.click(screen.getByRole("button", { name: "Sort by last update" }))
    expect(onSortChange).toHaveBeenLastCalledWith("-updated_at")
    fireEvent.click(screen.getByRole("button", { name: "Sort by name" }))
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
