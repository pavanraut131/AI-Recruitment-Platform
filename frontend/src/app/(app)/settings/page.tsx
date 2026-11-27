import type { Metadata } from "next"
import { SettingsIcon } from "lucide-react"

import { ComingSoon } from "@/components/coming-soon"

export const metadata: Metadata = { title: "Settings" }

export default function SettingsPage() {
  return (
    <ComingSoon
      title="Settings"
      icon={SettingsIcon}
      description="Your profile, your team and the agency's settings come next. Until then, edit a campaign from its Brief tab."
    />
  )
}
