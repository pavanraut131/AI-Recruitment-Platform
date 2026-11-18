import { ChartColumnIcon, UploadIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"

import type { Campaign } from "../api"
import { EmptyState } from "../components/data-states"

/*
 * Designed placeholders for the workspace tabs whose screens land later:
 * Uploads and Insights, in Sprint 6.
 */

function Soon({ label }: { label: string }) {
  return (
    <Badge variant="secondary" className="font-normal">
      {label}
    </Badge>
  )
}


export function UploadsPlaceholder({ campaign }: { campaign: Campaign }) {
  const { resumes, in_progress: inProgress, failed } = campaign.counts
  return (
    <EmptyState
      icon={UploadIcon}
      title="Uploads"
      description={
        <>
          Drop PDFs here and watch each one move from queued to scored, with retry for failures. This campaign has{" "}
          <span className="tabular-nums">{resumes}</span> {resumes === 1 ? "resume" : "resumes"}
          {inProgress > 0 && (
            <>
              , <span className="tabular-nums">{inProgress}</span> processing
            </>
          )}
          {failed > 0 && (
            <>
              , <span className="tabular-nums">{failed}</span> failed
            </>
          )}
          .
        </>
      }
      action={<Soon label="Arrives in Sprint 6" />}
    />
  )
}

export function InsightsPlaceholder() {
  return (
    <EmptyState
      icon={ChartColumnIcon}
      title="Insights"
      description="Category distribution, score histogram, top skills against the required skills, and the processing funnel."
      action={<Soon label="Arrives in Sprint 6" />}
    />
  )
}
