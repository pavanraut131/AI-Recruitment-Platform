import type { Metadata } from "next"
import { Suspense } from "react"

import { Home } from "@/features/home"

export const metadata: Metadata = { title: "Home" }

/**
 * Home (spec §4): active campaigns, uploads in progress and recent strong
 * matches. It follows the client choice in the URL (`?client=`, nuqs →
 * useSearchParams), so it renders inside a Suspense boundary.
 */
export default function HomePage() {
  return (
    <Suspense>
      <Home />
    </Suspense>
  )
}
