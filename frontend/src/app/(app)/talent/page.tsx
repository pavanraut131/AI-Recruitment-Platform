import type { Metadata } from "next"
import { Suspense } from "react"

import { TalentList } from "@/features/people"

export const metadata: Metadata = { title: "Talent" }

// The list reads its search and page from the URL (nuqs → useSearchParams), so
// it renders inside a Suspense boundary.
export default function TalentPage() {
  return (
    <Suspense>
      <TalentList />
    </Suspense>
  )
}
