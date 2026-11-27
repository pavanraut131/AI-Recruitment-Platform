import type { Metadata } from "next"
import { Instrument_Sans } from "next/font/google"

import { ThemeProvider } from "@/components/app-shell/theme-provider"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { DataProviders } from "@/lib/query/provider"
import "./globals.css"

// Instrument Sans for everything (docs/design/README.md). next/font downloads it at build
// time and serves it from the app, so pages never call Google Fonts at runtime.
const instrumentSans = Instrument_Sans({
  subsets: ["latin", "latin-ext"],
  variable: "--font-instrument-sans",
  display: "swap",
})

export const metadata: Metadata = {
  title: {
    default: "HireFlow AI",
    template: "%s · HireFlow AI",
  },
  description:
    "HireFlow AI screens and ranks resumes for recruitment agencies: upload a batch, get a ranked shortlist, review every candidate as a person across campaigns.",
  applicationName: "HireFlow AI",
}

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    // suppressHydrationWarning: next-themes sets the theme class on <html>
    // before hydration (no flash), so server and client markup differ there.
    <html
      lang="en"
      className={instrumentSans.variable}
      suppressHydrationWarning
    >
      <body>
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-lg focus:bg-background focus:px-3 focus:py-2 focus:text-sm focus:shadow-md"
        >
          Skip to content
        </a>
        <ThemeProvider>
          <DataProviders>
            <TooltipProvider>
              {/* (app): the gate and the shell; (auth): the sign-in pages' centred layout. */}
              {children}
              <Toaster position="bottom-right" />
            </TooltipProvider>
          </DataProviders>
        </ThemeProvider>
      </body>
    </html>
  )
}
