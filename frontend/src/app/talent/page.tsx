import type { Metadata } from "next"
import { UsersIcon } from "lucide-react"

import { ComingSoon } from "@/components/coming-soon"

export const metadata: Metadata = { title: "Talent" }

export default function TalentPage() {
  return (
    <ComingSoon
      title="Talent"
      icon={UsersIcon}
      description="Everyone who has applied to any campaign, as one list of people. Arrives with E1-08."
    />
  )
}
