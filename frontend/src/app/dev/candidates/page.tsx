import type { Metadata } from "next"
import { notFound } from "next/navigation"

import { CandidatesFixtureView } from "@/features/applications/dev"

export const metadata: Metadata = { title: "Candidates table (dev)" }

const MAX_FIXTURE_ROWS = 2000

/**
 * Development only (production builds answer 404, like /dev/ui): the
 * candidates table over synthetic rows, with a React commit-time readout, to
 * re-run the 500-row check (EPIC-01 §8). `?rows=` sets the row count
 * (default 500, at most 2000). The real tab lives at `/campaigns/[id]`.
 */
export default async function DevCandidatesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>
}) {
  if (process.env.NODE_ENV === "production") notFound()
  const { rows } = await searchParams
  const count = Number(Array.isArray(rows) ? rows[0] : rows)
  const rowCount = Number.isInteger(count) && count > 0 ? Math.min(count, MAX_FIXTURE_ROWS) : 500
  return (
    <div className="flex w-full flex-col gap-4 px-4 pt-6 pb-10 sm:px-8">
      <h1 className="text-title font-semibold tracking-tight">Candidates table · fixture</h1>
      <CandidatesFixtureView rowCount={rowCount} />
    </div>
  )
}
