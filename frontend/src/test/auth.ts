import type { QueryClient } from "@tanstack/react-query"
import { vi } from "vitest"

import type { Schema } from "@/lib/api/client"
import { queryKeys } from "@/lib/api/query-keys"

/*
 * Test helpers for the sign-in pages, the auth gate and the user menu: a
 * signed-in `Me`, an invitation preview, and a fake `/api/v2` on `fetch` that
 * answers by method and path and records each request (with its headers and
 * credentials mode, which the session depends on).
 */

export const AGENCY = { id: "a0000000-0000-4000-8000-000000000001", name: "Northbank Talent" }
export const OTHER_AGENCY = { id: "a0000000-0000-4000-8000-000000000002", name: "Default agency" }

export function meFixture(overrides: Partial<Schema<"Me">> = {}): Schema<"Me"> {
  return {
    user: { id: "u0000000-0000-4000-8000-000000000001", email: "priya@northbank.example", display_name: "Priya Shah" },
    memberships: [{ agency: AGENCY, role: "recruiter" }],
    active_agency: AGENCY,
    role: "recruiter",
    permissions: ["workspace:read", "campaign:write"],
    client_scope: { all_clients: true, client_ids: [] },
    csrf_token: "csrf-from-me",
    ...overrides,
  }
}

type Role = Schema<"Role">
type Permission = Schema<"Permission">

/**
 * Each role's row of the permission matrix (EPIC-02 §5.2), as `Me.permissions`
 * lists it: what permission-aware screens are tested with.
 */
export const ROLE_PERMISSIONS: Record<Role, Permission[]> = {
  owner: [
    "workspace:read",
    "campaign:write",
    "campaign:delete",
    "resume:upload",
    "application:review",
    "application:delete",
    "application:export",
    "client:read",
    "client:manage",
    "team:read",
    "team:manage",
    "team:manage_privileged",
    "agency:manage",
  ],
  admin: [
    "workspace:read",
    "campaign:write",
    "campaign:delete",
    "resume:upload",
    "application:review",
    "application:delete",
    "application:export",
    "client:read",
    "client:manage",
    "team:read",
    "team:manage",
  ],
  recruiter: [
    "workspace:read",
    "campaign:write",
    "resume:upload",
    "application:review",
    "application:delete",
    "application:export",
    "client:read",
  ],
  viewer: ["workspace:read", "client:read"],
}

/** A signed-in `Me` with `role` and its permissions in {@link AGENCY}. */
export function meAs(role: Role, overrides: Partial<Schema<"Me">> = {}): Schema<"Me"> {
  return meFixture({ role, permissions: ROLE_PERMISSIONS[role], memberships: [{ agency: AGENCY, role }], ...overrides })
}

/**
 * Caches `me` as the auth gate would have loaded it, so screens inside the
 * shell (`useSignedInUser`, `usePermissions`) see a signed-in user. Feature
 * tests render as an owner unless they pass another `Me`.
 */
export function signInForTest(queryClient: QueryClient, me: Schema<"Me"> | null = meAs("owner")): void {
  if (me) queryClient.setQueryData(queryKeys.me.all, me)
}

export function invitationFixture(overrides: Partial<Schema<"InvitationPreview">> = {}): Schema<"InvitationPreview"> {
  return {
    agency: AGENCY,
    invited_by: "Olu Owner",
    email: "new.person@northbank.example",
    role: "recruiter",
    account_exists: false,
    expires_at: "2026-12-04T12:00:00Z",
    ...overrides,
  }
}

/** One request the fake API answered. */
export type RecordedCall = {
  method: string
  /** Relative to `/api/v2`, e.g. `/auth/login`. */
  path: string
  url: URL
  body: unknown
  headers: Headers
  credentials: RequestCredentials
}

type Handler = (call: RecordedCall) => Response | Promise<Response>

/**
 * Stubs `fetch` with routes like `"POST /auth/login"`. An unrouted request
 * gets a 599, so it fails loudly. Returns the calls made so far.
 */
export function installApi(routes: Record<string, Handler>): RecordedCall[] {
  const calls: RecordedCall[] = []
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url)
      const text = request.method === "GET" ? "" : await request.clone().text()
      let body: unknown = undefined
      try {
        body = text ? JSON.parse(text) : undefined
      } catch {
        body = text
      }
      const call: RecordedCall = {
        method: request.method,
        path: url.pathname.replace(/^\/api\/v2/, ""),
        url,
        body,
        headers: request.headers,
        credentials: request.credentials,
      }
      calls.push(call)
      const handler = routes[`${call.method} ${call.path}`]
      return handler ? handler(call) : new Response(`No route for ${call.method} ${call.path}`, { status: 599 })
    }),
  )
  return calls
}

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json", ...headers },
  })
}

export function problem(status: number, detail?: string, extra: Record<string, unknown> = {}, headers: Record<string, string> = {}) {
  const titles: Record<number, string> = {
    401: "Unauthorized",
    403: "Forbidden",
    409: "Conflict",
    410: "Gone",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
  }
  return json({ type: "about:blank", title: titles[status] ?? "Error", status, detail, ...extra }, status, headers)
}

/** No content (logout, reset confirm). */
export function noContent(status = 204): Response {
  return new Response(null, { status })
}

/** Removes the readable CSRF cookie the test setup puts in place. */
export function clearCsrfCookie(): void {
  document.cookie = "hf_csrf=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT"
}
