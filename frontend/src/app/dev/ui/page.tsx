import type { Metadata } from "next"
import { notFound } from "next/navigation"

import { ComponentGallery } from "./_components/component-gallery"

export const metadata: Metadata = { title: "UI gallery" }

/**
 * Component gallery (EPIC-01 §2: instead of Storybook). Development only:
 * production builds answer 404, so the gallery is never served.
 */
export default function UiGalleryPage() {
  if (process.env.NODE_ENV === "production") notFound()
  return <ComponentGallery />
}
