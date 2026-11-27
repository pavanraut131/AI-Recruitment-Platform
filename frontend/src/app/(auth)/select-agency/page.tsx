import type { Metadata } from "next"

import { SelectAgency } from "@/features/auth"
import { safeNextPath } from "@/lib/auth/paths"

export const metadata: Metadata = { title: "Choose an agency" }

// Typed by hand rather than with Next's generated `PageProps` (see /campaigns/[id]).
export default async function SelectAgencyPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>
}) {
  const { next } = await searchParams
  return <SelectAgency next={safeNextPath(Array.isArray(next) ? next[0] : next)} />
}
