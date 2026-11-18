import type { Metadata } from "next"
import { notFound } from "next/navigation"
import { Suspense } from "react"

import { CandidatesTab } from "@/features/applications"
import { CandidatesFixtureView } from "@/features/applications/dev"

export const metadata: Metadata = { title: "Candidates (dev)" }

const MAX_FIXTURE_ROWS = 2000

/**
 * Development only (production builds answer 404, like /dev/ui): the
 * Candidates tab (E1-05) on its own until the campaign workspace (E1-04) mounts
 * it. Temporary: removed once the tab is wired into /campaigns/[id].
 *
 * - `/dev/candidates/<campaign id>`: the tab against the API.
 * - `/dev/candidates/fixture?rows=500`: the table over synthetic rows, with a
 *   React commit-time readout (the 500-row check, EPIC-01 §8).
 */
export default async function DevCandidatesPage({
  params,
  searchParams,
}: {
  params: Promise<{ campaignId: string }>
  searchParams: Promise<Record<string, string | string[] | undefined>>
}) {
  if (process.env.NODE_ENV === "production") notFound()
  const { campaignId } = await params

  if (campaignId === "fixture") {
    const { rows } = await searchParams
    const count = Number(Array.isArray(rows) ? rows[0] : rows)
    const rowCount = Number.isInteger(count) && count > 0 ? Math.min(count, MAX_FIXTURE_ROWS) : 500
    return (
      <div className="flex w-full flex-col gap-4 px-6 py-6">
        <h1 className="text-xl font-semibold tracking-tight">Candidates table · fixture</h1>
        <CandidatesFixtureView rowCount={rowCount} />
      </div>
    )
  }

  return (
    <div className="flex w-full flex-col gap-4 px-6 py-6">
      <h1 className="text-xl font-semibold tracking-tight">Candidates (dev route)</h1>
      {/* nuqs reads the search params on the client. */}
      <Suspense>
        <CandidatesTab campaignId={campaignId} />
      </Suspense>
    </div>
  )
}
