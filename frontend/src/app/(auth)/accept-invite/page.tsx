import type { Metadata } from "next"

import { AcceptInvitation } from "@/features/auth"

// The invitation token is in the URL fragment; no-referrer keeps the page's
// URL out of any request it makes (EPIC-02 §9.1).
export const metadata: Metadata = { title: "Accept your invitation", referrer: "no-referrer" }

export default function AcceptInvitePage() {
  return <AcceptInvitation />
}
