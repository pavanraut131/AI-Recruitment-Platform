const DEFAULT_API_URL = "http://localhost:8000"

/** The API's origin (`NEXT_PUBLIC_API_URL`, baked in at build time), without a trailing slash. */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL || DEFAULT_API_URL).replace(/\/+$/, "")

/**
 * Root of the `/api/v2` application (ADR 0008). The generated paths are
 * relative to it (`/campaigns`, …), as the document's `servers` entry says.
 */
export const API_V2_BASE_URL = `${API_URL}/api/v2`
