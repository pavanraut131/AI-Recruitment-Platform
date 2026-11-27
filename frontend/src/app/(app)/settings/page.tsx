import { redirect } from "next/navigation"

/** `/settings` opens its first section, which every member has. */
export default function SettingsPage() {
  redirect("/settings/profile")
}
