import type { Metadata } from "next"
import { Suspense } from "react"

import { CampaignList } from "@/features/campaigns"

export const metadata: Metadata = { title: "Campaigns" }

// The list reads its filters from the URL (nuqs → useSearchParams), so it
// renders inside a Suspense boundary.
export default function CampaignsPage() {
  return (
    <Suspense>
      <CampaignList />
    </Suspense>
  )
}
