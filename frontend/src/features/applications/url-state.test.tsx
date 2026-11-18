import { act, renderHook } from "@testing-library/react"
import { NuqsTestingAdapter, type UrlUpdateEvent } from "nuqs/adapters/testing"
import type { ReactNode } from "react"
import { describe, expect, it, vi } from "vitest"

import {
  candidatesSearchParams,
  hasActiveFilters,
  nextSort,
  sortToSorting,
  toListParams,
  useCandidatesUrlState,
  type CandidatesUrlState,
} from "./url-state"

const DEFAULTS: CandidatesUrlState = {
  q: "",
  cat: [],
  min: null,
  max: null,
  skill: "",
  stage: [],
  sort: "-score",
  page: 1,
  size: 50,
  open: null,
}

describe("toListParams", () => {
  it("sends only the default sort and page for an empty URL", () => {
    expect(toListParams(DEFAULTS)).toEqual({ sort: "-score", page: 1, page_size: 50 })
    expect(hasActiveFilters(DEFAULTS)).toBe(false)
  })

  it("maps every filter to the API's query parameters", () => {
    const state: CandidatesUrlState = {
      ...DEFAULTS,
      q: "  ada ",
      cat: ["strong", "weak", "strong"],
      min: 60,
      max: 90,
      skill: " react ",
      stage: ["screened", "offer"],
      sort: "-updated_at",
      page: 3,
      size: 100,
    }
    expect(toListParams(state)).toEqual({
      q: "ada",
      category: ["strong_match", "weak_match"],
      min_score: 60,
      max_score: 90,
      skill: "react",
      stage: ["screened", "offer"],
      sort: "-updated_at",
      page: 3,
      page_size: 100,
    })
    expect(hasActiveFilters(state)).toBe(true)
  })

  it("keeps out-of-range values from reaching the API (it would answer 422)", () => {
    expect(toListParams({ ...DEFAULTS, min: -5, max: 250, page: 0, q: "   " })).toEqual({
      min_score: 0,
      max_score: 100,
      sort: "-score",
      page: 1,
      page_size: 50,
    })
  })
})

describe("sorting", () => {
  it("flips score and updated, starting descending", () => {
    expect(nextSort("score", "-score")).toBe("score")
    expect(nextSort("score", "score")).toBe("-score")
    expect(nextSort("score", "name")).toBe("-score")
    expect(nextSort("updated", "-score")).toBe("-updated_at")
    expect(nextSort("updated", "-updated_at")).toBe("updated_at")
    expect(nextSort("updated", "updated_at")).toBe("-updated_at")
  })

  it("sorts names A→Z only and toggles back to the default", () => {
    expect(nextSort("name", "-score")).toBe("name")
    expect(nextSort("name", "name")).toBe("-score")
  })

  it("maps each server sort to TanStack's sorting state", () => {
    expect(sortToSorting("-score")).toEqual({ id: "score", desc: true })
    expect(sortToSorting("score")).toEqual({ id: "score", desc: false })
    expect(sortToSorting("-updated_at")).toEqual({ id: "updated", desc: true })
    expect(sortToSorting("updated_at")).toEqual({ id: "updated", desc: false })
    expect(sortToSorting("name")).toEqual({ id: "name", desc: false })
  })
})

function setup(searchParams = "") {
  const onUrlUpdate = vi.fn<(event: UrlUpdateEvent) => void>()
  const wrapper = ({ children }: { children: ReactNode }) => (
    <NuqsTestingAdapter searchParams={searchParams} onUrlUpdate={onUrlUpdate} hasMemory>
      {children}
    </NuqsTestingAdapter>
  )
  const hook = renderHook(() => useCandidatesUrlState(), { wrapper })
  const lastQuery = () => onUrlUpdate.mock.lastCall?.[0].queryString
  return { ...hook, onUrlUpdate, lastQuery }
}

describe("useCandidatesUrlState", () => {
  it("restores every filter, the sort and the page from the URL (a reload)", () => {
    const { result } = setup(
      "?q=ada&cat=strong,moderate&min=60&max=95&skill=react&stage=screened,technical&sort=name&page=3&size=100&open=abc",
    )
    expect(result.current.state).toEqual({
      q: "ada",
      cat: ["strong", "moderate"],
      min: 60,
      max: 95,
      skill: "react",
      stage: ["screened", "technical"],
      sort: "name",
      page: 3,
      size: 100,
      open: "abc",
    })
    expect(result.current.listParams).toEqual({
      q: "ada",
      category: ["strong_match", "moderate_match"],
      min_score: 60,
      max_score: 95,
      skill: "react",
      stage: ["screened", "technical"],
      sort: "name",
      page: 3,
      page_size: 100,
    })
  })

  it("falls back to defaults for values it does not know", () => {
    const { result } = setup("?cat=excellent&stage=lunch&sort=salary&page=abc&size=7&min=high")
    expect(result.current.state).toEqual({ ...DEFAULTS, cat: [], stage: [] })
  })

  it("writes filters with short keys, drops empty ones and goes back to page 1", async () => {
    const { result, lastQuery } = setup("?page=4&q=old")
    await act(() => result.current.setFilters({ q: "", cat: ["strong", "moderate"], min: 70 }))
    expect(lastQuery()).toBe("?cat=strong,moderate&min=70")
    expect(result.current.state.page).toBe(1)
  })

  it("leaves defaults out of the URL and resets the page on sort and page size", async () => {
    const { result, lastQuery } = setup("?page=2")
    await act(() => result.current.setSort("score"))
    expect(lastQuery()).toBe("?sort=score")
    await act(() => result.current.setPage(3))
    expect(lastQuery()).toBe("?sort=score&page=3")
    await act(() => result.current.setPageSize(100))
    expect(lastQuery()).toBe("?sort=score&size=100")
    await act(() => result.current.setSort("-score"))
    expect(lastQuery()).toBe("?size=100")
  })

  it("clears the filters but keeps the sort and page size", async () => {
    const { result, lastQuery } = setup("?q=ada&cat=weak&min=10&max=20&skill=go&stage=offer&sort=name&size=25&page=2")
    await act(() => result.current.clearFilters())
    expect(lastQuery()).toBe("?sort=name&size=25")
  })

  it("does not use the workspace's `tab` key", () => {
    expect(Object.keys(candidatesSearchParams)).not.toContain("tab")
  })
})
