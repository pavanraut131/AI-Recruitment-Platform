import { QueryClientProvider } from "@tanstack/react-query"
import { render } from "@testing-library/react"
import { NuqsTestingAdapter, type OnUrlUpdateFunction } from "nuqs/adapters/testing"
import type { ReactElement } from "react"

import type { Schema } from "@/lib/api/client"
import { makeQueryClient } from "@/lib/query/query-client"
import { clientFixture } from "@/test/api"
import { AGENCY, meAs, signInForTest } from "@/test/auth"

/*
 * Test helpers for Settings: the app's QueryClient (no retries) with a
 * signed-in user cached as the auth gate leaves it, nuqs's testing adapter,
 * and team fixtures. The fake API is `mockRoutes` from `@/test/api`.
 */

export const ACME = clientFixture({ id: "00000000-0000-4000-8000-00000000c002", name: "Acme Corp" })
export const FINCH = clientFixture({ id: "00000000-0000-4000-8000-00000000c003", name: "Finch Pay" })
export const OLD_CO = clientFixture({ id: "00000000-0000-4000-8000-00000000c004", name: "Old Co", status: "archived" })

/** The signed-in owner of the fixtures (`meAs("owner")`'s user). */
export const ME = meAs("owner").user

export function renderSettings(
  ui: ReactElement,
  {
    me = meAs("owner"),
    searchParams = "",
    onUrlUpdate,
  }: { me?: Schema<"Me">; searchParams?: string; onUrlUpdate?: OnUrlUpdateFunction } = {},
) {
  const queryClient = makeQueryClient()
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } })
  signInForTest(queryClient, me)
  const view = render(
    <QueryClientProvider client={queryClient}>
      <NuqsTestingAdapter searchParams={searchParams} onUrlUpdate={onUrlUpdate} hasMemory>
        {ui}
      </NuqsTestingAdapter>
    </QueryClientProvider>,
  )
  return { ...view, queryClient }
}

let sequence = 0

export function memberFixture(overrides: Partial<Schema<"Member">> & { name?: string; email?: string } = {}): Schema<"Member"> {
  sequence += 1
  const { name = `Member ${sequence}`, email = `member${sequence}@northbank.example`, ...rest } = overrides
  return {
    id: `m0000000-0000-4000-8000-${String(sequence).padStart(12, "0")}`,
    user: { id: `u1000000-0000-4000-8000-${String(sequence).padStart(12, "0")}`, email, display_name: name },
    role: "recruiter",
    all_clients: true,
    clients: [],
    status: "active",
    last_active_at: null,
    joined_at: "2026-11-01T09:00:00Z",
    deactivated_at: null,
    ...rest,
  }
}

/** The signed-in owner's own row. */
export function selfMember(overrides: Partial<Schema<"Member">> = {}): Schema<"Member"> {
  return memberFixture({ user: ME, role: "owner", last_active_at: "2026-11-27T08:00:00Z", ...overrides })
}

export function teamInvitationFixture(overrides: Partial<Schema<"Invitation">> = {}): Schema<"Invitation"> {
  sequence += 1
  return {
    id: `i0000000-0000-4000-8000-${String(sequence).padStart(12, "0")}`,
    email: `invitee${sequence}@northbank.example`,
    role: "recruiter",
    all_clients: true,
    clients: [],
    invited_by: { id: ME.id, email: ME.email, display_name: ME.display_name },
    status: "pending",
    expires_at: "2099-12-04T12:00:00Z",
    created_at: "2026-11-27T12:00:00Z",
    ...overrides,
  }
}

export { AGENCY }
