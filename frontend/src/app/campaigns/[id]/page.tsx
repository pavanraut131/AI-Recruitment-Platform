import type { Metadata } from "next"
import { Suspense } from "react"

import { CampaignWorkspace, CandidatesPlaceholder } from "@/features/campaigns"

export const metadata: Metadata = { title: "Campaign" }

/**
 * The campaign workspace. The Candidates tab is a slot: until E1-05's table
 * (`features/applications`) is wired in, it shows a placeholder.
 */
// Typed by hand rather than with Next's generated `PageProps`: CI runs `tsc`
// before `next build`, when the generated route types do not exist yet.
export default async function CampaignPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  return (
    <Suspense>
      <CampaignWorkspace campaignId={id} candidatesTab={<CandidatesPlaceholder />} />
    </Suspense>
  )
}
