import { cleanup } from "@testing-library/react"
import { afterEach, beforeEach } from "vitest"

import { forgetCsrfToken } from "@/lib/api/csrf"

// The API's readable CSRF cookie, as a signed-in browser has it, so unsafe
// requests carry X-CSRF-Token without fetching a token first
// (src/lib/api/csrf.ts; its own tests remove it). Not in `node` test files.
beforeEach(() => {
  if (typeof document !== "undefined") document.cookie = "hf_csrf=vitest-csrf-token; path=/"
})

// Unmount what each test rendered (Vitest runs without globals, so Testing
// Library cannot register this itself), and forget any token a test fetched.
afterEach(() => {
  cleanup()
  forgetCsrfToken()
})
