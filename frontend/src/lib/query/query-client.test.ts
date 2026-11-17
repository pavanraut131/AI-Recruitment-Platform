import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { ApiProblem } from "@/lib/api/problem"

import { DEFAULT_STALE_TIME_MS, makeQueryClient, MAX_RETRIES, shouldRetry } from "./query-client"

const { toastError } = vi.hoisted(() => ({ toastError: vi.fn() }))
vi.mock("sonner", () => ({ toast: { error: toastError } }))

const problem = (status: number, detail?: string) =>
  new ApiProblem({ status, title: `HTTP ${status}`, detail })

describe("shouldRetry", () => {
  it("retries network failures and 5xx, up to the limit", () => {
    expect(shouldRetry(0, problem(0))).toBe(true)
    expect(shouldRetry(0, problem(500))).toBe(true)
    expect(shouldRetry(2, problem(503))).toBe(true)
    expect(shouldRetry(MAX_RETRIES, problem(503))).toBe(false)
  })

  it("never retries a 4xx or an error that is not an ApiProblem", () => {
    for (const status of [400, 404, 409, 422]) expect(shouldRetry(0, problem(status))).toBe(false)
    expect(shouldRetry(0, new TypeError("x is undefined"))).toBe(false)
  })
})

describe("makeQueryClient", () => {
  beforeEach(() => {
    toastError.mockClear()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it("sets the default stale time and retry policy", () => {
    const options = makeQueryClient().getDefaultOptions()
    expect(options.queries?.staleTime).toBe(DEFAULT_STALE_TIME_MS)
    expect(options.queries?.retry).toBe(shouldRetry)
    expect(options.queries?.refetchOnWindowFocus).toBe(true)
    expect(options.mutations?.retry).toBe(false)
  })

  it("does not toast a query's first failure (the view shows its error state)", async () => {
    const client = makeQueryClient()
    await client
      .fetchQuery({ queryKey: ["first"], queryFn: () => Promise.reject(problem(404, "Campaign not found")) })
      .catch(() => undefined)
    expect(toastError).not.toHaveBeenCalled()
  })

  it("toasts the problem detail when a refetch fails while data is shown", async () => {
    const client = makeQueryClient()
    client.setQueryData(["shown"], { ok: true })
    await client
      .fetchQuery({
        queryKey: ["shown"],
        queryFn: () => Promise.reject(problem(404, "Campaign not found")),
        staleTime: 0,
      })
      .catch(() => undefined)
    expect(toastError).toHaveBeenCalledTimes(1)
    expect(toastError).toHaveBeenCalledWith("HTTP 404", expect.objectContaining({ description: "Campaign not found" }))
  })

  it("follows a query's errorToast meta", async () => {
    const client = makeQueryClient()
    await client
      .fetchQuery({ queryKey: ["forced"], queryFn: () => Promise.reject(problem(400, "Bad")), meta: { errorToast: true } })
      .catch(() => undefined)
    expect(toastError).toHaveBeenCalledTimes(1)

    client.setQueryData(["quiet"], 1)
    await client
      .fetchQuery({
        queryKey: ["quiet"],
        queryFn: () => Promise.reject(problem(400)),
        staleTime: 0,
        meta: { errorToast: false },
      })
      .catch(() => undefined)
    expect(toastError).toHaveBeenCalledTimes(1)
  })

  it("toasts every failed mutation unless its meta opts out", async () => {
    const client = makeQueryClient()
    const run = (meta?: { errorToast?: boolean }) =>
      client
        .getMutationCache()
        .build(client, { mutationFn: () => Promise.reject(problem(409, "Already moved")), meta })
        .execute(undefined)
        .catch(() => undefined)

    await run()
    expect(toastError).toHaveBeenCalledWith("HTTP 409", expect.objectContaining({ description: "Already moved" }))
    await run({ errorToast: false })
    expect(toastError).toHaveBeenCalledTimes(1)
  })

  it("collapses identical errors into one toast id", async () => {
    const client = makeQueryClient()
    for (const key of ["a", "b"]) {
      await client
        .fetchQuery({ queryKey: [key], queryFn: () => Promise.reject(problem(0)), meta: { errorToast: true }, retry: false })
        .catch(() => undefined)
    }
    const ids = toastError.mock.calls.map(([, options]) => (options as { id: string }).id)
    expect(ids).toHaveLength(2)
    expect(ids[0]).toBe(ids[1])
  })
})
