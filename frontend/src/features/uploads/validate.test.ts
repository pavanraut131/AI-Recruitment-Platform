import { describe, expect, it } from "vitest"

import { MAX_FILE_SIZE_BYTES, MAX_FILES_PER_BATCH } from "./limits"
import { checkFile, formatBytes, perFileProgress, toBatches } from "./validate"

describe("checkFile (the server's checks before it reads a file)", () => {
  it("accepts a PDF name in any case, up to the size limit inclusive", () => {
    expect(checkFile({ name: "ada.pdf", size: 1 })).toBeNull()
    expect(checkFile({ name: "ADA.PDF", size: MAX_FILE_SIZE_BYTES })).toBeNull()
  })

  it("rejects other extensions with the server's wording, before the size", () => {
    expect(checkFile({ name: "notes.docx", size: 1 })).toBe("Only PDF files are accepted")
    expect(checkFile({ name: "resume.pdf.exe", size: MAX_FILE_SIZE_BYTES + 1 })).toBe("Only PDF files are accepted")
    expect(checkFile({ name: "resume", size: 1 })).toBe("Only PDF files are accepted")
  })

  it("rejects a file over MAX_FILE_SIZE_MB with the server's wording", () => {
    expect(MAX_FILE_SIZE_BYTES).toBe(10 * 1024 * 1024)
    expect(checkFile({ name: "big.pdf", size: MAX_FILE_SIZE_BYTES + 1 })).toBe("File exceeds 10MB limit")
  })
})

describe("toBatches", () => {
  it("splits into requests of at most MAX_FILES_PER_BATCH (50) files, in order", () => {
    const files = Array.from({ length: 120 }, (_, index) => index)
    const batches = toBatches(files)
    expect(MAX_FILES_PER_BATCH).toBe(50)
    expect(batches.map((batch) => batch.length)).toEqual([50, 50, 20])
    expect(batches.flat()).toEqual(files)
  })

  it("gives no batch for no files and one for a small drop", () => {
    expect(toBatches([])).toEqual([])
    expect(toBatches(["a", "b"])).toEqual([["a", "b"]])
  })
})

describe("perFileProgress", () => {
  it("fills the files in the order they are sent", () => {
    expect(perFileProgress([100, 300], 0)).toEqual([0, 0])
    expect(perFileProgress([100, 300], 0.125)).toEqual([0.5, 0])
    expect(perFileProgress([100, 300], 0.5)).toEqual([1, 1 / 3])
    expect(perFileProgress([100, 300], 1)).toEqual([1, 1])
  })

  it("clamps the fraction and copes with empty files", () => {
    expect(perFileProgress([100], 1.2)).toEqual([1])
    expect(perFileProgress([0, 0], 0.5)).toEqual([0.5, 0.5])
  })
})

describe("formatBytes", () => {
  it("uses B, KB and MB", () => {
    expect(formatBytes(840)).toBe("840 B")
    expect(formatBytes(312 * 1024)).toBe("312 KB")
    expect(formatBytes(2.4 * 1024 * 1024)).toBe("2.4 MB")
  })
})
