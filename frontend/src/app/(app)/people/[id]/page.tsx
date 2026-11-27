import type { Metadata } from "next"
import { Suspense } from "react"

import { PersonProfile } from "@/features/people"

export const metadata: Metadata = { title: "Person" }

/**
 * A Person's profile across campaigns (spec §5.4). Thin: the people feature
 * does the work. `?application=<id>` picks the application shown.
 */
// Typed by hand rather than with Next's generated `PageProps`: CI runs `tsc`
// before `next build`, when the generated route types do not exist yet.
export default async function PersonPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  return (
    // nuqs reads the search params on the client (useSearchParams needs a Suspense boundary).
    <Suspense>
      <PersonProfile personId={id} />
    </Suspense>
  )
}
