import type { ReactElement } from "react"
import { describe, expect, it } from "vitest"

import { CandidateRedirect } from "@/features/applications"

import CandidatePage from "./page"

describe("/candidates/[id]", () => {
  it("forwards the old candidate link's id to the redirect as an application id", async () => {
    const page = (await CandidatePage({ params: Promise.resolve({ id: "a-1" }) })) as ReactElement<{
      applicationId: string
    }>
    expect(page.type).toBe(CandidateRedirect)
    expect(page.props.applicationId).toBe("a-1")
  })
})
