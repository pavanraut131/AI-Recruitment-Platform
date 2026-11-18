import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"

import { CAMPAIGN_STATUS_LABELS, type CampaignStatus } from "@/lib/campaigns"

const DOT: Record<CampaignStatus, string> = {
  active: "bg-primary",
  closed: "bg-muted-foreground",
  archived: "border border-muted-foreground bg-transparent",
}

/** Campaign status as a quiet outline badge with a dot: teal for active, grey otherwise. */
export function CampaignStatusBadge({ status, className }: { status: CampaignStatus; className?: string }) {
  return (
    <Badge variant="outline" className={cn("gap-1.5 text-muted-foreground", status === "active" && "text-foreground", className)}>
      <span aria-hidden="true" className={cn("size-1.5 rounded-full", DOT[status])} />
      {CAMPAIGN_STATUS_LABELS[status]}
    </Badge>
  )
}
