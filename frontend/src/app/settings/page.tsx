import type { Metadata } from "next"
import { SettingsIcon } from "lucide-react"

import { ComingSoon } from "@/components/coming-soon"

export const metadata: Metadata = { title: "Settings" }

export default function SettingsPage() {
  return (
    <ComingSoon
      title="Settings"
      icon={SettingsIcon}
      description="Agency and account settings arrive with sign-in. Until then, edit a campaign from its Brief tab."
    />
  )
}
