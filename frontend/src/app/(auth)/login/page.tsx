import type { Metadata } from "next"

import { Login } from "@/features/auth"
import { safeNextPath } from "@/lib/auth/paths"

export const metadata: Metadata = { title: "Sign in" }

type SearchParams = Record<string, string | string[] | undefined>

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value
}

/**
 * `/login?next=…&reason=…&error=…&provider=…` (`error` and `provider` come
 * back from a Google or Microsoft sign-in). The query is read on the server,
 * so the form renders at once; `next` is checked here (a path on this site
 * only).
 */
// Typed by hand rather than with Next's generated `PageProps` (see /campaigns/[id]).
export default async function LoginPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = await searchParams
  return (
    <Login
      next={safeNextPath(first(params.next))}
      reason={first(params.reason)}
      error={first(params.error)}
      provider={first(params.provider)}
    />
  )
}
