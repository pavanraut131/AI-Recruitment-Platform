import { SettingsLayout } from "@/features/settings"

/** `/settings/*` (EPIC-02 §9.1): Profile, Team, Clients and Agency, as tabs the role can use. */
export default function Layout({ children }: { children: React.ReactNode }) {
  return <SettingsLayout>{children}</SettingsLayout>
}
