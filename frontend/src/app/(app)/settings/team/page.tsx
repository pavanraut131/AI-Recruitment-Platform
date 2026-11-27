import type { Metadata } from "next"

import { TeamSettings } from "@/features/settings"

export const metadata: Metadata = { title: "Team" }

export default function TeamPage() {
  return <TeamSettings />
}
