import { LogoMark } from "@/components/app-shell/logo"
import { cn } from "@/lib/utils"

/**
 * The sign-in pages' frame (EPIC-02 §9.1): no sidebar, the product's mark,
 * and one panel centred on the paper. One column at every width, 16px
 * gutters on a phone.
 */
export function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main
      id="main-content"
      tabIndex={-1}
      className="flex min-h-svh flex-col items-center bg-canvas px-4 pt-10 pb-16 outline-none sm:justify-center sm:pt-16"
    >
      <div className="flex w-full max-w-[400px] flex-col gap-6">
        <div className="flex items-center justify-center gap-2.5">
          <LogoMark className="size-7" />
          <span className="text-section font-semibold tracking-tight">HireFlow AI</span>
        </div>
        <div className="rounded-xl border bg-background px-5 py-6 sm:px-8 sm:py-8">{children}</div>
      </div>
    </main>
  )
}

/** A sign-in page's title and the sentence under it. */
export function AuthHeader({
  title,
  children,
  className,
}: {
  title: string
  children?: React.ReactNode
  className?: string
}) {
  return (
    <header className={cn("flex flex-col gap-1.5", className)}>
      <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
      {children && <div className="text-ink-2">{children}</div>}
    </header>
  )
}
