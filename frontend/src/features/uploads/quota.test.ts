import { describe, expect, it } from "vitest"

import { nextQuotaReset } from "./quota"

describe("nextQuotaReset (midnight in America/Los_Angeles)", () => {
  it("is the coming midnight Pacific time", () => {
    // 10:00 PST on 18 Nov = 18:00Z; next midnight PST = 08:00Z on 19 Nov.
    expect(nextQuotaReset(new Date("2026-11-18T18:00:00Z")).toISOString()).toBe("2026-11-19T08:00:00.000Z")
    // Just after midnight: the next one, nearly a day away.
    expect(nextQuotaReset(new Date("2026-11-19T08:00:01Z")).toISOString()).toBe("2026-11-20T08:00:00.000Z")
  })

  it("lands on midnight across a change to daylight time (a 23-hour day)", () => {
    // 01:00 PST on 8 Mar 2026; clocks go forward at 02:00, so midnight on 9 Mar is 07:00Z (PDT).
    expect(nextQuotaReset(new Date("2026-03-08T09:00:00Z")).toISOString()).toBe("2026-03-09T07:00:00.000Z")
  })

  it("lands on midnight across the change back (a 25-hour day)", () => {
    // 01:30 PDT on 1 Nov 2026; clocks go back at 02:00, so midnight on 2 Nov is 08:00Z (PST).
    expect(nextQuotaReset(new Date("2026-11-01T08:30:00Z")).toISOString()).toBe("2026-11-02T08:00:00.000Z")
  })
})
