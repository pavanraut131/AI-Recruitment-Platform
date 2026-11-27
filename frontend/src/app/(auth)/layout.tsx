import { AuthLayout } from "@/components/auth/auth-layout"

/**
 * The sign-in pages (EPIC-02 §9.1): `/login`, `/forgot-password`,
 * `/reset-password`, `/accept-invite` and `/select-agency`. Outside the app
 * shell, in a centred layout.
 */
export default function AuthPagesLayout({ children }: { children: React.ReactNode }) {
  return <AuthLayout>{children}</AuthLayout>
}
