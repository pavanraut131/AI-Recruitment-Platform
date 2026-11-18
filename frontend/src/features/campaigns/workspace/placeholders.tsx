import { ChartColumnIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"

import { EmptyState } from "../components/data-states"

/*
 * Designed placeholder for the workspace tab whose screen lands later:
 * Insights, in Sprint 6.
 */

function Soon({ label }: { label: string }) {
  return (
    <Badge variant="secondary" className="font-normal">
      {label}
    </Badge>
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
