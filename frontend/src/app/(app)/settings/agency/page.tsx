import type { Metadata } from "next"

import { AgencySettings } from "@/features/settings"

export const metadata: Metadata = { title: "Agency" }

export default function AgencyPage() {
  return <AgencySettings />
}
