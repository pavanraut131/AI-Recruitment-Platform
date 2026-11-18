import type { Metadata } from "next"

import { Home } from "@/features/home"

export const metadata: Metadata = { title: "Home" }

/** Home (spec §4): active campaigns, uploads in progress and recent strong matches. */
export default function HomePage() {
  return <Home />
}
