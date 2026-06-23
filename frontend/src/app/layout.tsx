import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import "react-toastify/dist/ReactToastify.css";
import { ToastContainer } from "react-toastify";
import Sidebar from "@/components/Sidebar";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "ResumeAI — Bulk Resume Processing",
  description: "AI-powered bulk resume import, extraction, and intelligent candidate categorization platform for modern recruitment teams.",
  keywords: ["resume", "AI", "recruitment", "candidate", "bulk import"],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable} suppressHydrationWarning>
      <body className="bg-[#08090c] bg-premium-pattern text-white antialiased">
        <Sidebar />
        <main className="main-content">
          {children}
        </main>
        <ToastContainer theme="dark" position="bottom-right" />
      </body>
    </html>
  );
}
