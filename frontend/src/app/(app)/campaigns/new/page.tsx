import type { Metadata } from "next"
import { Suspense } from "react"

import { NewCampaignForm } from "@/features/campaigns"

export const metadata: Metadata = { title: "New campaign" }

// The form preselects the client from the URL (`?client=`, nuqs → useSearchParams),
// so it renders inside a Suspense boundary.
export default function NewCampaignPage() {
  return (
    <Suspense>
      <NewCampaignForm />
    </Suspense>
  )
}
