import type { Metadata } from "next"

import { NewCampaignForm } from "@/features/campaigns"

export const metadata: Metadata = { title: "New campaign" }

export default function NewCampaignPage() {
  return <NewCampaignForm />
}
