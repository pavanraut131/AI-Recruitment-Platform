import { Skeleton } from "@/components/ui/skeleton"
import { AuthHeader } from "@/components/auth/auth-layout"

/** While an emailed link is read (or checked with the API). */
export function LinkLoading({ label }: { label: string }) {
  return (
    <div aria-busy="true" className="flex flex-col gap-6">
      <span className="sr-only">{label}</span>
      <div className="flex flex-col gap-2">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-4 w-full" />
      </div>
      <div className="flex flex-col gap-3">
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-full" />
      </div>
    </div>
  )
}

/**
 * An emailed link that cannot be used: no token in it (cut short by an email
 * client), or one the API refused (410: expired, used, replaced). What
 * happened, then what to do (docs/design/README.md, voice).
 */
export function LinkProblem({
  title,
  children,
  action,
}: {
  title: string
  children: React.ReactNode
  action: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-6">
      <AuthHeader title={title}>{children}</AuthHeader>
      {action}
    </div>
  )
}
