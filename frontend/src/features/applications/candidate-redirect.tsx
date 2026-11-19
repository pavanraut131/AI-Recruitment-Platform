"use client"

import { useQuery } from "@tanstack/react-query"
import { ArrowLeftIcon, SearchXIcon } from "lucide-react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useEffect, type ReactNode } from "react"

import { EmptyState, ErrorState } from "@/components/data-states"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { isApiProblem } from "@/lib/api/client"
import { applicationQueryOptions } from "@/lib/query/applications"
import { applicationHref } from "@/lib/routes"

/** Not found, or an id that is not a UUID (422): the same thing to a reader. */
function isMissing(error: unknown): boolean {
  return isApiProblem(error) && (error.status === 404 || error.status === 422)
}

/**
 * `/candidates/[id]`, the old UI's candidate page (E1-10): the old candidate
 * id is the application's id, so the link still works. It looks the
 * application up (`getApplication`) and replaces the URL with the application
 * in its campaign, the review panel open (`/campaigns/<id>?open=<id>`).
 */
export function CandidateRedirect({ applicationId }: { applicationId: string }) {
  const router = useRouter()
  const application = useQuery(applicationQueryOptions(applicationId))
  const campaignId = application.data?.campaign_id

  useEffect(() => {
    if (campaignId) router.replace(applicationHref(campaignId, applicationId))
  }, [router, campaignId, applicationId])

  if (application.isError) {
    return (
      <Frame>
        {isMissing(application.error) ? (
          <EmptyState
            icon={SearchXIcon}
            title="Candidate not found"
            description="They may have been removed, or the link is wrong."
            action={<BackToCampaigns />}
          />
        ) : (
          <ErrorState
            error={application.error}
            onRetry={() => void application.refetch()}
            retrying={application.isFetching}
            action={<BackToCampaigns />}
          />
        )}
      </Frame>
    )
  }

  return (
    <Frame>
      <div className="flex flex-col gap-2" aria-busy="true" aria-label="Opening candidate">
        <Skeleton className="h-7 w-64" />
        <Skeleton className="h-4 w-80" />
      </div>
    </Frame>
  )
}

function Frame({ children }: { children: ReactNode }) {
  return <div className="mx-auto flex w-full max-w-5xl flex-col gap-8 px-6 py-8">{children}</div>
}

function BackToCampaigns() {
  return (
    <Button variant="outline" asChild>
      <Link href="/campaigns">
        <ArrowLeftIcon data-icon="inline-start" />
        Campaigns
      </Link>
    </Button>
  )
}
