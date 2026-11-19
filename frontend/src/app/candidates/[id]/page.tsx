import type { Metadata } from "next"

import { CandidateRedirect } from "@/features/applications"

export const metadata: Metadata = { title: "Candidate" }

/**
 * The old UI's candidate link (`/candidates/<id>`, deleted in E1-10). The id
 * is an application's, so this forwards to it in its campaign.
 */
// Typed by hand rather than with Next's generated `PageProps`: CI runs `tsc`
// before `next build`, when the generated route types do not exist yet.
export default async function CandidatePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  return <CandidateRedirect applicationId={id} />
}
