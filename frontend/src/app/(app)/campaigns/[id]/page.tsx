import type { Metadata } from "next"
import { Suspense } from "react"

import { CandidatesTab } from "@/features/applications"
import { CampaignWorkspace } from "@/features/campaigns"
import { UploadsTab } from "@/features/uploads"

export const metadata: Metadata = { title: "Campaign" }

/**
 * The campaign workspace. The route composes the features: the workspace
 * (`features/campaigns`) takes the Candidates tab (`features/applications`)
 * and the Uploads tab (`features/uploads`) as slots, so no feature imports
 * another.
 */
// Typed by hand rather than with Next's generated `PageProps`: CI runs `tsc`
// before `next build`, when the generated route types do not exist yet.
export default async function CampaignPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  return (
    <Suspense>
      <CampaignWorkspace
        campaignId={id}
        candidatesTab={<CandidatesTab campaignId={id} />}
        uploadsTab={<UploadsTab campaignId={id} />}
      />
    </Suspense>
  )
}
