import type { Metadata } from "next"
import { Geist, Geist_Mono } from "next/font/google"

import { AppShell } from "@/components/app-shell/app-shell"
import { ThemeProvider } from "@/components/app-shell/theme-provider"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { DataProviders } from "@/lib/query/provider"
import { cn } from "@/lib/utils"
import "./globals.css"

const geistSans = Geist({ subsets: ["latin"], variable: "--font-geist-sans" })
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono" })

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
      className={cn(geistSans.variable, geistMono.variable)}
      suppressHydrationWarning
    >
      <body>
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2 focus:text-sm focus:shadow-md"
        >
          Skip to content
        </a>
        <ThemeProvider>
          <DataProviders>
            <TooltipProvider>
              <AppShell>{children}</AppShell>
              <Toaster position="bottom-right" />
            </TooltipProvider>
          </DataProviders>
        </ThemeProvider>
      </body>
    </html>
  )
}
