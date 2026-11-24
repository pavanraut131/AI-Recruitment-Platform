import { cn } from "@/lib/utils"

import { CAMPAIGN_STATUS_LABELS, type CampaignStatus } from "@/lib/campaigns"

const DOT: Record<CampaignStatus, string> = {
  active: "bg-primary",
  closed: "bg-muted-foreground",
  archived: "border border-muted-foreground bg-transparent",
}

/** Campaign status as a dot and a word, no box: the accent dot for active, grey otherwise. */
export function CampaignStatusBadge({
  status,
  className,
  children,
}: {
  status: CampaignStatus
  className?: string
  /** Replaces the label (e.g. "Active since 12 Nov"); keep the status word in it. */
  children?: React.ReactNode
}) {
  return (
    <span
      data-slot="campaign-status"
      className={cn(
        "inline-flex items-center gap-1.5 text-dense whitespace-nowrap text-muted-foreground",
        status === "active" && "text-ink-2",
        className,
      )}
    >
      <span aria-hidden="true" className={cn("size-[7px] shrink-0 rounded-full", DOT[status])} />
      {children ?? CAMPAIGN_STATUS_LABELS[status]}
    </span>
  )
}
