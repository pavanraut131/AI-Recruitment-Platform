import { act, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { AUTOSAVE_DELAY_MS, clearUnsavedDrafts, useAutosave } from "./use-autosave"

beforeEach(() => {
  vi.useFakeTimers()
})
afterEach(() => {
  vi.useRealTimers()
  clearUnsavedDrafts()
})

/** A save whose calls stay open until resolved or rejected by the test. */
function controlledSave() {
  const calls: { value: string; resolve: () => void; reject: (error: unknown) => void }[] = []
  const save = vi.fn(
    (value: string) =>
      new Promise<void>((resolve, reject) => {
        calls.push({ value, resolve, reject })
      }),
  )
  return { save, calls }
}

function setup(initial = "", save = controlledSave().save, onUnmountedError = vi.fn()) {
  return renderHook(
    ({ value }) => useAutosave({ key: "notes:1", value, save, onUnmountedError }),
    { initialProps: { value: initial } },
  )
}

describe("useAutosave", () => {
  it("saves once, after a pause in typing", async () => {
    const { save, calls } = controlledSave()
    const { result } = setup("", save)
    expect(result.current.status).toBe("idle")

    act(() => result.current.onChange("Hel"))
    act(() => vi.advanceTimersByTime(AUTOSAVE_DELAY_MS - 1))
    act(() => result.current.onChange("Hello"))
    expect(result.current.status).toBe("pending")
    act(() => vi.advanceTimersByTime(AUTOSAVE_DELAY_MS - 1))
    expect(save).not.toHaveBeenCalled()

    act(() => vi.advanceTimersByTime(1))
    expect(save).toHaveBeenCalledTimes(1)
    expect(save).toHaveBeenCalledWith("Hello")
    expect(result.current.status).toBe("saving")

    await act(async () => calls[0].resolve())
    expect(result.current.status).toBe("saved")
    expect(result.current.draft).toBe("Hello")
  })

  it("saves text typed during a save after it, never out of order", async () => {
    const { save, calls } = controlledSave()
    const { result } = setup("", save)
    act(() => result.current.onChange("first"))
    act(() => vi.advanceTimersByTime(AUTOSAVE_DELAY_MS))
    act(() => result.current.onChange("first, second"))
    act(() => vi.advanceTimersByTime(AUTOSAVE_DELAY_MS))
    // The second save waits for the first.
    expect(save).toHaveBeenCalledTimes(1)

    await act(async () => calls[0].resolve())
    expect(save).toHaveBeenCalledTimes(2)
    expect(save).toHaveBeenLastCalledWith("first, second")
    expect(result.current.status).toBe("saving")
    await act(async () => calls[1].resolve())
    expect(result.current.status).toBe("saved")
  })

  it("keeps the draft and the error when a save fails, and retries on flush", async () => {
    const { save, calls } = controlledSave()
    const { result } = setup("", save)
    act(() => result.current.onChange("keep me"))
    act(() => vi.advanceTimersByTime(AUTOSAVE_DELAY_MS))
    const failure = new Error("offline")
    await act(async () => calls[0].reject(failure))
    expect(result.current.status).toBe("error")
    expect(result.current.error).toBe(failure)
    expect(result.current.draft).toBe("keep me")

    act(() => void result.current.flush())
    expect(save).toHaveBeenCalledTimes(2)
    await act(async () => calls[1].resolve())
    expect(result.current.status).toBe("saved")
    expect(result.current.error).toBeNull()
  })

  it("does not save text that is back to the saved value", () => {
    const { save } = controlledSave()
    const { result } = setup("same", save)
    act(() => result.current.onChange("sam"))
    act(() => result.current.onChange("same"))
    act(() => vi.advanceTimersByTime(AUTOSAVE_DELAY_MS))
    expect(save).not.toHaveBeenCalled()
    expect(result.current.status).toBe("idle")
  })

  it("flushes on unmount; a failure there is reported and the text kept for the next editor", async () => {
    const { save, calls } = controlledSave()
    const onUnmountedError = vi.fn()
    const first = setup("", save, onUnmountedError)
    act(() => first.result.current.onChange("unsaved"))
    first.unmount()
    // Saved at once, without waiting for the pause.
    expect(save).toHaveBeenCalledWith("unsaved")
    await act(async () => calls[0].reject(new Error("down")))
    expect(onUnmountedError).toHaveBeenCalledTimes(1)

    const second = setup("", save, onUnmountedError)
    expect(second.result.current.draft).toBe("unsaved")
    expect(second.result.current.status).toBe("pending")
    act(() => vi.advanceTimersByTime(0))
    expect(save).toHaveBeenCalledTimes(2)
    await act(async () => calls[1].resolve())
    expect(second.result.current.status).toBe("saved")
  })

  it("adopts a new server value only when nothing local is pending", () => {
    const { save } = controlledSave()
    const { result, rerender } = setup("v1", save)
    rerender({ value: "v2" })
    expect(result.current.draft).toBe("v2")

    act(() => result.current.onChange("local edit"))
    rerender({ value: "v3" })
    expect(result.current.draft).toBe("local edit")
  })
})
