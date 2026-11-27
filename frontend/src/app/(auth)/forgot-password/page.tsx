import type { Metadata } from "next"

import { ForgotPassword } from "@/features/auth"

export const metadata: Metadata = { title: "Reset your password" }

export default function ForgotPasswordPage() {
  return <ForgotPassword />
}
