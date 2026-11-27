import type { Metadata } from "next"

import { ResetPassword } from "@/features/auth"

// The reset token is in the URL fragment; no-referrer keeps the page's URL out
// of any request it makes (EPIC-02 §9.1).
export const metadata: Metadata = { title: "Choose a new password", referrer: "no-referrer" }

export default function ResetPasswordPage() {
  return <ResetPassword />
}
