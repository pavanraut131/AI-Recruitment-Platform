"use client"

import { useQuery } from "@tanstack/react-query"
import { ArrowLeftIcon, SearchXIcon } from "lucide-react"
import Link from "next/link"
import { parseAsString, useQueryState } from "nuqs"
import { useRef, type ReactNode } from "react"

import { EmptyState, ErrorState } from "@/components/data-states"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { isApiProblem } from "@/lib/api/client"
import { useFocusShortcut } from "@/lib/keyboard"
import { applicationQueryOptions } from "@/lib/query/applications"
import { personQueryOptions } from "@/lib/query/people"

import { ConflictsBanner } from "./conflicts-banner"
import { selectedApplication } from "./model"
import { ProfileHeader } from "./profile-header"
import { ApplicationsSection, EvaluationSection, NotesSection, ProfileSection } from "./sections"

/**
 * The selected application, in the URL (`?application=<id>`) so a profile
 * link can open on one application and the back button returns to the
 * previous one. Left out for the default (the most recent application).
 */
const applicationParam = parseAsString.withOptions({ history: "push", scroll: false })

/** Not found, or an id that is not a UUID (422): the same thing to a reader. */
function isMissing(error: unknown): boolean {
  return isApiProblem(error) && (error.status === 404 || error.status === 422)
}

/**
 * `/people/[id]` (spec §5.4): one Person across campaigns. The header has
 * their contact and links and an application switcher; the evaluation, stage
 * and notes below are the selected application's.
 */
export function PersonProfile({ personId }: { personId: string }) {
  const person = useQuery(personQueryOptions(personId))
  const [requested, setRequested] = useQueryState("application", applicationParam)

  const applications = person.data?.applications ?? []
  const selected = selectedApplication(applications, requested)
  const application = useQuery({
    ...applicationQueryOptions(selected?.id ?? ""),
    enabled: selected !== undefined,
  })

  const stageTrigger = useRef<HTMLButtonElement>(null)
  useFocusShortcut("s", stageTrigger, selected !== undefined)

  if (person.isPending) return <ProfileSkeleton />

  if (person.isError) {
    return (
      <ProfileFrame>
        {isMissing(person.error) ? (
          <EmptyState
            icon={SearchXIcon}
            title="Person not found"
            description="They may have been removed, or the link is wrong."
            action={<BackToTalent />}
          />
        ) : (
          <ErrorState
            error={person.error}
            onRetry={() => void person.refetch()}
            retrying={person.isFetching}
            action={<BackToTalent />}
          />
        )}
      </ProfileFrame>
    )
  }

  const data = person.data
  const onSelect = (applicationId: string) =>
    // The most recent application is the default: keep the URL clean for it.
    void setRequested(applicationId === applications[0]?.id ? null : applicationId)

  return (
    <ProfileFrame>
      <ProfileHeader person={data} selected={selected} onSelect={onSelect} stageTrigger={stageTrigger} />
      {data.has_unresolved_conflicts && <ConflictsBanner person={data} />}
      <EvaluationSection selected={selected} application={application} />
      <ProfileSection person={data} />
      <ApplicationsSection applications={applications} selectedId={selected?.id} onSelect={onSelect} />
      <NotesSection personId={data.id} selected={selected} application={application} />
    </ProfileFrame>
  )
}

function ProfileFrame({ children }: { children: ReactNode }) {
  return <div className="mx-auto flex w-full max-w-5xl flex-col gap-8 px-6 py-8">{children}</div>
}

function BackToTalent() {
  return (
    <Button variant="outline" asChild>
      <Link href="/talent">
        <ArrowLeftIcon data-icon="inline-start" />
        Talent
      </Link>
    </Button>
  )
}

function ProfileSkeleton() {
  return (
    <ProfileFrame>
      <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading person">
        <Skeleton className="h-7 w-64" />
        <Skeleton className="h-4 w-80" />
        <Skeleton className="mt-2 h-8 w-96" />
      </div>
      <Skeleton className="h-48 w-full" />
      <Skeleton className="h-32 w-full" />
    </ProfileFrame>
  )
}
