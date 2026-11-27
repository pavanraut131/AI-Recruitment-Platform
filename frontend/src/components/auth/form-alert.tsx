import { AlertCircleIcon, CheckCircle2Icon, InfoIcon } from "lucide-react"

import { cn } from "@/lib/utils"

const TONES = {
  error: { role: "alert", icon: AlertCircleIcon, className: "bg-destructive/10 text-destructive" },
  info: { role: "status", icon: InfoIcon, className: "bg-muted text-ink-2" },
  success: { role: "status", icon: CheckCircle2Icon, className: "bg-primary-soft text-foreground [&>svg]:text-primary" },
} as const

/**
 * One message above or below a form (EPIC-02 §9.1): why a sign-in failed, why
 * the login page opened ("Your session ended"), that a password was changed.
 * Errors are announced at once (`alert`), the rest politely (`status`).
 */
export function FormAlert({
  tone = "error",
  title,
  children,
  className,
}: {
  tone?: keyof typeof TONES
  title?: string
  children?: React.ReactNode
  className?: string
}) {
  const { role, icon: Icon, className: toneClass } = TONES[tone]
  return (
    <div role={role} className={cn("flex items-start gap-2.5 rounded-lg px-3 py-2.5 text-sm", toneClass, className)}>
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <div className="flex min-w-0 flex-col gap-0.5">
        {title && <p className="font-medium">{title}</p>}
        {children && <div>{children}</div>}
      </div>
    </div>
  )
}
