import type { LucideIcon } from "lucide-react"

/** Placeholder for shell routes whose screen lands in a later ticket. */
export function ComingSoon({
  title,
  description,
  icon: Icon,
}: {
  title: string
  description: string
  icon: LucideIcon
}) {
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-8 px-4 pt-6 pb-14 sm:px-8 lg:px-14">
      <h1 className="text-title font-semibold tracking-tight">{title}</h1>
      <div className="flex flex-col items-center gap-3 border-t px-6 py-16 text-center">
        <div className="flex size-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <Icon className="size-5" aria-hidden="true" />
        </div>
        <p className="max-w-sm text-ink-2">{description}</p>
      </div>
    </div>
  )
}
