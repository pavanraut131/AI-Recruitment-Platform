import { isValidElement, type ReactElement } from "react"
import { describe, expect, it } from "vitest"

import { CandidatesTab } from "@/features/applications"
import { CampaignWorkspace } from "@/features/campaigns"
import { UploadsTab } from "@/features/uploads"

import CampaignPage from "./page"

type WorkspaceProps = Parameters<typeof CampaignWorkspace>[0]

describe("/campaigns/[id]", () => {
  it("composes the workspace with the Candidates and Uploads tabs in their slots", async () => {
    const page = (await CampaignPage({ params: Promise.resolve({ id: "c-1" }) })) as ReactElement<{
      children: ReactElement<WorkspaceProps>
    }>
    const workspace = page.props.children

    expect(workspace.type).toBe(CampaignWorkspace)
    expect(workspace.props.campaignId).toBe("c-1")
    for (const [slot, component] of [
      [workspace.props.candidatesTab, CandidatesTab],
      [workspace.props.uploadsTab, UploadsTab],
    ] as const) {
      expect(isValidElement(slot)).toBe(true)
      expect((slot as ReactElement).type).toBe(component)
      expect((slot as ReactElement<{ campaignId: string }>).props.campaignId).toBe("c-1")
    }
  })
})
