import { AppShell } from "@/components/app-shell/app-shell"
import { AuthGate } from "@/components/auth/auth-gate"

/**
 * The product's pages (EPIC-02 §9.1): for a signed-in user only (the gate
 * loads `/me` first), inside the app shell.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGate>
      <AppShell>{children}</AppShell>
    </AuthGate>
  )
}
