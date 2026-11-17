"use client"

import { ThemeProvider as NextThemesProvider } from "next-themes"

/** System / light / dark via a class on <html>; no flash on load. */
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
    >
      {children}
    </NextThemesProvider>
  )
}
