import { API_V2_BASE_URL, ApiProblem, type Schema, type components, type paths } from "@/lib/api/client"
import { networkProblem, problemFromResponse } from "@/lib/api/problem"

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
 * when there was no response).
 */
export function postUpload(
  campaignId: string,
  files: readonly File[],
  { onProgress }: UploadOptions = {},
): Promise<UploadResult> {
  return new Promise<UploadResult>((resolve, reject) => {
    const request = new XMLHttpRequest()
    request.open("POST", uploadUrl(campaignId))
    request.responseType = "text"

    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable && event.total > 0) onProgress?.(event.loaded / event.total)
    })
    request.addEventListener("load", () => {
      void settle(request).then(resolve, reject)
    })
    request.addEventListener("error", () => reject(networkProblem()))
    request.addEventListener("timeout", () => reject(networkProblem()))
    request.addEventListener("abort", () => reject(networkProblem()))

    const body = new FormData()
    for (const file of files) body.append(FILES_FIELD, file, file.name)
    request.send(body)
  })
}

async function settle(request: XMLHttpRequest): Promise<UploadResult> {
  // `load` with status 0 (or outside 200–599) is not an HTTP answer a `Response` can hold.
  if (request.status < 200 || request.status > 599) throw networkProblem()
  const nullBody = request.status === 204 || request.status === 205 || request.status === 304
  const response = new Response(nullBody ? null : request.responseText, {
    status: request.status,
    statusText: request.statusText,
    headers: { "Content-Type": request.getResponseHeader("Content-Type") ?? "" },
  })
  if (!response.ok) throw await problemFromResponse(response)
  try {
    return (await response.json()) as UploadResult
  } catch (cause) {
    throw new ApiProblem({ status: response.status, title: "Unexpected response", cause })
  }
}
