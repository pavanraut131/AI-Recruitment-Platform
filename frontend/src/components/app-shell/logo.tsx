import { cn } from "@/lib/utils"

/**
 * HireFlow AI mark: a teal tile with three ranked bars (a shortlist).
 * Colours come from tokens; src/app/icon.svg is the same mark for the favicon.
 */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      aria-hidden="true"
      className={cn("size-8 shrink-0", className)}
    >
      <rect width="32" height="32" rx="7" className="fill-primary" />
      <g className="fill-primary-foreground">
        <rect x="8" y="9" width="16" height="3" rx="1.5" />
        <rect x="8" y="14.5" width="12" height="3" rx="1.5" />
        <rect x="8" y="20" width="8" height="3" rx="1.5" />
      </g>
    </svg>
  )
}
