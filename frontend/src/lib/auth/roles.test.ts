import { describe, expect, it } from "vitest"

import { firstName, initials } from "./roles"
import { waitInWords } from "./wait"

describe("initials", () => {
  it("takes the first and last word, leaving out a part in brackets", () => {
    expect(initials("Priya Shah")).toBe("PS")
    expect(initials("Ana María de la Cruz")).toBe("AC")
    expect(initials("Recruiter (Default agency)")).toBe("R")
    expect(initials("  ")).toBe("?")
  })
})

describe("firstName", () => {
  it("is the first word of the display name", () => {
    expect(firstName("Priya Shah")).toBe("Priya")
    expect(firstName("  Olu ")).toBe("Olu")
    expect(firstName("")).toBeNull()
  })
})

describe("waitInWords", () => {
  it("rounds up to seconds, minutes or hours", () => {
    expect(waitInWords(1)).toBe("1 second")
    expect(waitInWords(30)).toBe("30 seconds")
    expect(waitInWords(60)).toBe("1 minute")
    expect(waitInWords(61)).toBe("2 minutes")
    expect(waitInWords(840)).toBe("14 minutes")
    expect(waitInWords(3600)).toBe("1 hour")
    expect(waitInWords(3601)).toBe("2 hours")
  })
})
