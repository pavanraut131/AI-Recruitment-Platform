import { API_V2_BASE_URL, ApiProblem, type Schema, type components, type paths } from "@/lib/api/client"
import { CSRF_HEADER, currentCsrfToken, fetchCsrfToken, isCsrfFailure } from "@/lib/api/csrf"
import { networkProblem, problemFromResponse } from "@/lib/api/problem"
import { reportSessionProblems } from "@/lib/api/transport"

export type UploadResult = Schema<"UploadResult">

const UPLOAD_PATH = "/campaigns/{campaign_id}/uploads" satisfies keyof paths
/** The multipart field the endpoint reads (`Body_uploadResumes`). */
const FILES_FIELD = "files" satisfies keyof components["schemas"]["Body_uploadResumes"]

export function uploadUrl(campaignId: string, baseUrl: string = API_V2_BASE_URL): string {
  return `${baseUrl}${UPLOAD_PATH.replace("{campaign_id}", encodeURIComponent(campaignId))}`
}

export type UploadOptions = {
  /** Called as the body is sent, with the fraction (0–1) sent so far. */
  onProgress?: (fraction: number) => void
}

/**
 * `uploadResumes` (`POST /campaigns/{campaign_id}/uploads`, multipart field
 * `files`) with upload progress.
 *
 * The generated client (openapi-fetch, on `fetch`) cannot report upload
 * progress, and a batch can be up to 50 × 10 MB, so this one call uses
 * `XMLHttpRequest`, whose `upload.onprogress` reports bytes sent. It keeps the
 * client's contract (ADR 0006): it resolves with the typed 2xx body, and
 * rejects with an `ApiProblem` (parsed from the problem+json body, or status 0
 * when there was no response). It carries the session like the client does
 * (EPIC-02 §9.4): `withCredentials`, the CSRF header (sent again once with a
 * fresh token after a CSRF 403), and a 401 reported as an ended session.
 */
export function postUpload(
  campaignId: string,
  files: readonly File[],
  { onProgress }: UploadOptions = {},
): Promise<UploadResult> {
  const body = new FormData()
  for (const file of files) body.append(FILES_FIELD, file, file.name)
  const url = uploadUrl(campaignId)
  const token = currentCsrfToken()
  // Usually the token is at hand and the request starts right away; otherwise fetch one first.
  if (token !== null) return send(url, body, token, onProgress, true)
  return fetchCsrfToken().then((fetched) => send(url, body, fetched, onProgress, true))
}

function send(
  url: string,
  body: FormData,
  token: string,
  onProgress: UploadOptions["onProgress"],
  canRetry: boolean,
): Promise<UploadResult> {
  return new Promise<UploadResult>((resolve, reject) => {
    const request = new XMLHttpRequest()
    request.open("POST", url)
    request.withCredentials = true
    request.setRequestHeader(CSRF_HEADER, token)
    request.responseType = "text"

    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable && event.total > 0) onProgress?.(event.loaded / event.total)
    })
    request.addEventListener("load", () => {
      answer(request)
        .then(async (response) => {
          if (canRetry && (await isCsrfFailure(response))) {
            return send(url, body, await fetchCsrfToken(), onProgress, false)
          }
          await reportSessionProblems(response, url)
          return settle(response)
        })
        .then(resolve, reject)
    })
    request.addEventListener("error", () => reject(networkProblem()))
    request.addEventListener("timeout", () => reject(networkProblem()))
    request.addEventListener("abort", () => reject(networkProblem()))

    request.send(body)
  })
}

/** The XHR's answer as a `Response`, so it is read like the client's. */
async function answer(request: XMLHttpRequest): Promise<Response> {
  // `load` with status 0 (or outside 200–599) is not an HTTP answer a `Response` can hold.
  if (request.status < 200 || request.status > 599) throw networkProblem()
  const nullBody = request.status === 204 || request.status === 205 || request.status === 304
  const headers: Record<string, string> = { "Content-Type": request.getResponseHeader("Content-Type") ?? "" }
  const retryAfter = request.getResponseHeader("Retry-After")
  if (retryAfter !== null) headers["Retry-After"] = retryAfter
  return new Response(nullBody ? null : request.responseText, {
    status: request.status,
    statusText: request.statusText,
    headers,
  })
}

async function settle(response: Response): Promise<UploadResult> {
  if (!response.ok) throw await problemFromResponse(response)
  try {
    return (await response.json()) as UploadResult
  } catch (cause) {
    throw new ApiProblem({ status: response.status, title: "Unexpected response", cause })
  }
}
