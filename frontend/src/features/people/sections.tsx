"use client"

import type { UseQueryResult } from "@tanstack/react-query"
import { AwardIcon, BriefcaseIcon, FolderGit2Icon, GraduationCapIcon, SearchXIcon } from "lucide-react"
import Link from "next/link"
import type { ReactNode } from "react"

import { ApplicationEvaluation, EvaluationBlock, SkillChips } from "@/components/application/evaluation"
import { ApplicationNotes } from "@/components/application/review-controls"
import { CategoryBadge, ScoreCell } from "@/components/application/score"
import { EmptyState, ErrorState } from "@/components/data-states"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { isApiProblem } from "@/lib/api/client"
import { formatDate, stageLabel, type Application } from "@/lib/applications"
import { applicationHref } from "@/lib/routes"
import { cn } from "@/lib/utils"

import { educationEntries, projectEntries, workEntries, type Person, type PersonApplication } from "./model"

type ApplicationQuery = UseQueryResult<Application, Error>

/** A titled section of the profile (Evaluation, Profile, Applications, Notes). */
function Section({
  id,
  title,
  description,
  children,
}: {
  id: string
  title: string
  description?: ReactNode
  children: ReactNode
}) {
  const headingId = `${id}-heading`
  return (
    <section id={id} aria-labelledby={headingId} className="flex scroll-mt-4 flex-col gap-4">
      <div className="flex flex-col gap-0.5 border-b pb-2">
        <h2 id={headingId} className="text-lg font-semibold tracking-tight">
          {title}
        </h2>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {children}
    </section>
  )
}

function NoApplications() {
  return <p className="text-sm text-muted-foreground">No applications: their resumes were deleted from every campaign.</p>
}

/** The selected application's state, or `null` when it loaded (the section renders it). */
function applicationState(application: ApplicationQuery): ReactNode | null {
  if (application.isPending) {
    return (
      <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading application">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-24 w-full" />
      </div>
    )
  }
  if (application.isError) {
    return isApiProblem(application.error) && application.error.status === 404 ? (
      <EmptyState
        icon={SearchXIcon}
        title="Application not found"
        description="It was deleted meanwhile. Pick another application above."
        className="py-8"
      />
    ) : (
      <ErrorState
        error={application.error}
        onRetry={() => void application.refetch()}
        retrying={application.isFetching}
        className="py-8"
      />
    )
  }
  return null
}

/** The AI's evaluation of the selected application. */
export function EvaluationSection({
  selected,
  application,
}: {
  selected: PersonApplication | undefined
  application: ApplicationQuery
}) {
  if (!selected) {
    return (
      <Section id="evaluation" title="Evaluation">
        <NoApplications />
      </Section>
    )
  }
  const state = applicationState(application)
  const data = application.data
  return (
    <Section
      id="evaluation"
      title="Evaluation"
      description={
        <>
          For{" "}
          <Link href={applicationHref(selected.campaign.id, selected.id)} className="font-medium text-foreground hover:underline">
            {selected.campaign.title}
          </Link>
          , applied {formatDate(selected.created_at)}
        </>
      }
    >
      {state ??
        (data && (
          <div className="flex flex-col gap-5">
            <div className="flex flex-wrap items-center gap-4 text-sm">
              <ScoreCell score={data.score} category={data.category} />
              <CategoryBadge category={data.category} />
            </div>
            <ApplicationEvaluation application={data} />
          </div>
        ))}
    </Section>
  )
}

function EntryList({ children, label }: { children: ReactNode; label: string }) {
  return (
    <ul aria-label={label} className="flex flex-col gap-3">
      {children}
    </ul>
  )
}

function Entry({ title, subtitle, meta, children }: { title: ReactNode; subtitle?: ReactNode; meta?: ReactNode; children?: ReactNode }) {
  return (
    <li className="flex flex-col gap-0.5 text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <span className="font-medium">{title}</span>
        {meta && <span className="text-xs text-muted-foreground tabular-nums">{meta}</span>}
      </div>
      {subtitle && <span className="text-muted-foreground">{subtitle}</span>}
      {children}
    </li>
  )
}

/** What the AI extracted from the Person's most recent resume (any campaign). */
export function ProfileSection({ person }: { person: Person }) {
  const { profile } = person
  const work = workEntries(profile.work_experience)
  const education = educationEntries(profile.education)
  const projects = projectEntries(profile.projects)
  const certifications = profile.certifications.filter((item) => item.trim() !== "")

  return (
    <Section id="profile" title="Profile" description="Extracted from their most recent resume.">
      <EvaluationBlock title="Skills">
        <SkillChips skills={profile.skills} />
      </EvaluationBlock>
      <div className="grid gap-6 md:grid-cols-2">
        <EvaluationBlock title="Experience" icon={BriefcaseIcon}>
          {work.length === 0 ? (
            <p className="text-sm text-muted-foreground">No work experience extracted.</p>
          ) : (
            <EntryList label="Work experience">
              {work.map((entry, index) => (
                <Entry key={index} title={entry.role ?? entry.company} subtitle={entry.role ? entry.company : null} meta={entry.duration}>
                  {entry.description && <p className="text-muted-foreground">{entry.description}</p>}
                </Entry>
              ))}
            </EntryList>
          )}
        </EvaluationBlock>
        <EvaluationBlock title="Education" icon={GraduationCapIcon}>
          {education.length === 0 ? (
            <p className="text-sm text-muted-foreground">No education extracted.</p>
          ) : (
            <EntryList label="Education">
              {education.map((entry, index) => (
                <Entry
                  key={index}
                  title={entry.degree ?? entry.institution}
                  subtitle={entry.degree ? entry.institution : null}
                  meta={entry.year}
                />
              ))}
            </EntryList>
          )}
        </EvaluationBlock>
        <EvaluationBlock title="Projects" icon={FolderGit2Icon}>
          {projects.length === 0 ? (
            <p className="text-sm text-muted-foreground">No projects extracted.</p>
          ) : (
            <EntryList label="Projects">
              {projects.map((entry, index) => (
                <Entry key={index} title={entry.name ?? "Untitled project"}>
                  {entry.description && <p className="text-muted-foreground">{entry.description}</p>}
                  {entry.technologies.length > 0 && (
                    <p className="text-xs text-muted-foreground">{entry.technologies.join(" · ")}</p>
                  )}
                </Entry>
              ))}
            </EntryList>
          )}
        </EvaluationBlock>
        <EvaluationBlock title="Certifications" icon={AwardIcon}>
          {certifications.length === 0 ? (
            <p className="text-sm text-muted-foreground">No certifications extracted.</p>
          ) : (
            <ul aria-label="Certifications" className="flex flex-col gap-1.5 text-sm">
              {certifications.map((item, index) => (
                <li key={`${index}-${item}`}>{item}</li>
              ))}
            </ul>
          )}
        </EvaluationBlock>
      </div>
    </Section>
  )
}

/** Every application across campaigns: campaign, score, category, stage, applied date. */
export function ApplicationsSection({
  applications,
  selectedId,
  onSelect,
}: {
  applications: readonly PersonApplication[]
  selectedId: string | undefined
  onSelect: (applicationId: string) => void
}) {
  return (
    <Section id="applications" title="Applications" description="Every campaign this person applied to, newest first.">
      {applications.length === 0 ? (
        <NoApplications />
      ) : (
        <div className="rounded-lg border">
          <Table aria-label="Applications" className="text-dense">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="h-9 text-xs text-muted-foreground">Campaign</TableHead>
                <TableHead className="h-9 text-xs text-muted-foreground">Score</TableHead>
                <TableHead className="h-9 text-xs text-muted-foreground">Category</TableHead>
                <TableHead className="h-9 text-xs text-muted-foreground">Stage</TableHead>
                <TableHead className="h-9 text-xs text-muted-foreground">Applied</TableHead>
                <TableHead className="h-9 w-24 text-xs text-muted-foreground">
                  <span className="sr-only">Evaluation</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {applications.map((application) => {
                const current = application.id === selectedId
                return (
                  <TableRow
                    key={application.id}
                    data-state={current ? "selected" : undefined}
                    aria-current={current || undefined}
                  >
                    <TableCell className="py-2">
                      <div className="flex flex-col">
                        <Link
                          href={applicationHref(application.campaign.id, application.id)}
                          className="font-medium hover:underline"
                        >
                          {application.campaign.title}
                        </Link>
                        {application.campaign.status !== "active" && (
                          <span className="text-xs text-muted-foreground">Campaign {application.campaign.status}</span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <ScoreCell score={application.score} category={application.category} />
                    </TableCell>
                    <TableCell>
                      <CategoryBadge category={application.category} />
                    </TableCell>
                    <TableCell className={cn(application.pipeline_stage === null && "text-muted-foreground")}>
                      {stageLabel(application.pipeline_stage)}
                    </TableCell>
                    <TableCell className="text-muted-foreground tabular-nums">{formatDate(application.created_at)}</TableCell>
                    <TableCell className="text-right">
                      {current ? (
                        <span className="text-xs text-muted-foreground">Shown above</span>
                      ) : (
                        <Button
                          variant="ghost"
                          size="xs"
                          onClick={() => onSelect(application.id)}
                          aria-label={`Show the evaluation for ${application.campaign.title}`}
                        >
                          Show
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </Section>
  )
}

/** The selected application's notes, autosaved like the review panel's. */
export function NotesSection({
  personId,
  selected,
  application,
}: {
  personId: string
  selected: PersonApplication | undefined
  application: ApplicationQuery
}) {
  if (!selected) {
    return (
      <Section id="notes" title="Notes">
        <NoApplications />
      </Section>
    )
  }
  const state = applicationState(application)
  return (
    <Section id="notes" title="Notes" description={`On the application to ${selected.campaign.title}.`}>
      {state ??
        (application.data && (
          <ApplicationNotes
            // Keyed: switching application saves these notes and starts from the next one's.
            key={selected.id}
            target={{ applicationId: selected.id, campaignId: selected.campaign.id, personId }}
            notes={application.data.notes}
            label={`Notes · ${selected.campaign.title}`}
          />
        ))}
    </Section>
  )
}
