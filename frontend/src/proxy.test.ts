// @vitest-environment node
import { unstable_doesMiddlewareMatch } from "next/experimental/testing/server"
import { NextRequest } from "next/server"
import { describe, expect, it } from "vitest"

import { config, proxy, seesSessionCookie } from "./proxy"

const API = "http://localhost:8000"

function request(path: string, { cookie, host = "localhost:3000" }: { cookie?: string; host?: string } = {}) {
  const headers = new Headers({ host })
  if (cookie) headers.set("cookie", cookie)
  return new NextRequest(new URL(path, `http://${host}`), { headers })
}

function redirectOf(response: Response): string | null {
  return response.headers.get("location")
}

describe("proxy (an optimistic redirect, EPIC-02 §9.4)", () => {
  it("sends a request without a session cookie to /login, remembering the page", () => {
    const response = proxy(request("/campaigns/c-1?tab=uploads"))

    expect(response.status).toBe(307)
    expect(redirectOf(response)).toBe("http://localhost:3000/login?next=%2Fcampaigns%2Fc-1%3Ftab%3Duploads")
  })

  it("sends / to plain /login", () => {
    expect(redirectOf(proxy(request("/")))).toBe("http://localhost:3000/login")
  })

  it("lets a request with a session cookie through, whatever it is worth (/me decides)", () => {
    expect(redirectOf(proxy(request("/campaigns", { cookie: "hf_session=anything" })))).toBeNull()
    expect(redirectOf(proxy(request("/campaigns", { cookie: "__Host-hf_session=anything" })))).toBeNull()
  })

  it("does not count the readable CSRF cookie as a session", () => {
    expect(redirectOf(proxy(request("/campaigns", { cookie: "hf_csrf=token" })))).not.toBeNull()
  })

  it("never redirects the sign-in pages or the agency picker", () => {
    for (const path of ["/login", "/forgot-password", "/reset-password", "/accept-invite", "/select-agency"]) {
      expect(redirectOf(proxy(request(path)))).toBeNull()
    }
  })

  it("stays out of the way when the API is on another host (it cannot see the API's cookies)", () => {
    expect(seesSessionCookie(request("/", { host: "localhost:3102" }), API)).toBe(true)
    expect(seesSessionCookie(request("/", { host: "app.hireflow.example" }), "https://api.hireflow.example")).toBe(false)
    expect(seesSessionCookie(request("/", { host: "127.0.0.1:3000" }), API)).toBe(false)
  })

  it("runs on pages, not on Next's files, the API rewrite or icons", () => {
    const matches = (url: string) => unstable_doesMiddlewareMatch({ config, url })
    expect(matches("/")).toBe(true)
    expect(matches("/campaigns/c-1")).toBe(true)
    expect(matches("/_next/static/chunks/app.js")).toBe(false)
    expect(matches("/api/v2/me")).toBe(false)
    expect(matches("/icon.svg")).toBe(false)
    expect(matches("/favicon.ico")).toBe(false)
  })
})
