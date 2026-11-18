/*
 * Upload limits the server enforces, mirrored for the client-side checks
 * (spec §5.3). The API does not expose them (no settings or limits endpoint,
 * and the OpenAPI document has no maxItems / maxLength for the upload body),
 * so they are copied here by hand and must follow the backend:
 *
 * - `MAX_FILE_SIZE_MB`, `MAX_FILES_PER_BATCH`: `Settings` in
 *   backend/app/platform/config.py (defaults; a deployment can override them
 *   with environment variables, which this copy would not see).
 * - The `.pdf` name check and the rejection messages:
 *   `_accept_uploads` / `_rejection` in backend/app/modules/ingestion/service.py.
 * - The quota reset: `QUOTA_EXCEEDED_MESSAGE` in
 *   backend/app/modules/ingestion/tasks.py ("Quota resets at midnight PT").
 *
 * If the server's limits change and this file does not, the server still
 * decides: a file the client lets through is rejected with the server's
 * reason, shown inline like any other rejection.
 */

/** Largest accepted file, in MiB (`settings.MAX_FILE_SIZE_MB`). */
export const MAX_FILE_SIZE_MB = 10
/** The same, in bytes (`MAX_FILE_SIZE` in ingestion/service.py: MB × 1024 × 1024). */
export const MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

/**
 * Most files in one upload request (`settings.MAX_FILES_PER_BATCH`); more is
 * a 422. A larger drop is split into batches of this size, sent one after
 * another.
 */
export const MAX_FILES_PER_BATCH = 50

/** The server accepts a file by its name's extension (case-insensitive), then checks `%PDF`. */
export const PDF_EXTENSION = ".pdf"
/** For the file picker's filter (`<input accept>`). */
export const PDF_ACCEPT = "application/pdf,.pdf"

/** The server's rejection reasons, reused for the same checks on the client. */
export const REJECTION_REASONS = {
  type: "Only PDF files are accepted",
  size: `File exceeds ${MAX_FILE_SIZE_MB}MB limit`,
} as const

/** The AI provider's daily quota resets at midnight in this time zone (Gemini free tier). */
export const QUOTA_RESET_TIME_ZONE = "America/Los_Angeles"
