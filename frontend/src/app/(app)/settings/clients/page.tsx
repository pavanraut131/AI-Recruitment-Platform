import type { Metadata } from "next"
import { Suspense } from "react"

import { ClientsSettings } from "@/features/settings"

export const metadata: Metadata = { title: "Clients" }

// The list's search, status and page are in the URL (nuqs → useSearchParams).
export default function ClientsPage() {
  return (
    <Suspense>
      <ClientsSettings />
    </Suspense>
  )
}
