"use client"

import { BriefcaseIcon, ExternalLinkIcon, MailIcon, PhoneIcon } from "lucide-react"
import { useId, type ReactNode, type RefObject } from "react"

import { ApplicationStageSelect } from "@/components/application/review-controls"
import { Kbd } from "@/components/ui/kbd"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { displayName, formatDate, formatYears } from "@/lib/applications"

import { contacts, type Person, type PersonApplication } from "./model"

function ContactItem({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return <li className="inline-flex items-center gap-1.5">{icon}{children}</li>
}

function ExternalLink({ href, label }: { href: string; label: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 hover:text-foreground hover:underline"
    >
      {label}
      <ExternalLinkIcon className="size-3" aria-hidden="true" />
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  )
}

/** Only http(s) links from a resume become anchors. */
function safeUrl(url: string | null): string | null {
  if (!url) return null
  const withScheme = /^https?:\/\//i.test(url) ? url : `https://${url}`
  try {
    const parsed = new URL(withScheme)
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null
  } catch {
    return null
  }
}

/** "Data platform lead · 12 Nov 2026": an application in the switcher. */
function applicationLabel(application: PersonApplication): string {
  return `${application.campaign.title} · ${formatDate(application.created_at)}`
}

/**
 * Name, contact, links and experience, then the application switcher (the
 * evaluation, stage and notes below follow it) and the selected application's
 * stage.
 */
export function ProfileHeader({
  person,
  selected,
  onSelect,
  stageTrigger,
}: {
  person: Person
  selected: PersonApplication | undefined
  onSelect: (applicationId: string) => void
  stageTrigger: RefObject<HTMLButtonElement | null>
}) {
  const switcherId = useId()
  const stageId = useId()
  const { emails, phones } = contacts(person)
  const github = safeUrl(person.github_url)
  const linkedin = safeUrl(person.linkedin_url)
  const years = person.profile.years_of_experience

  return (
    <header className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">{displayName(person.name)}</h1>
        <ul aria-label="Contact and links" className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
          {emails.map((email) => (
            <ContactItem key={email} icon={<MailIcon className="size-3.5" aria-hidden="true" />}>
              <a href={`mailto:${email}`} className="hover:text-foreground hover:underline">
                {email}
              </a>
            </ContactItem>
          ))}
          {phones.map((phone) => (
            <ContactItem key={phone} icon={<PhoneIcon className="size-3.5" aria-hidden="true" />}>
              <a href={`tel:${phone.replace(/[^\d+]/g, "")}`} className="hover:text-foreground hover:underline">
                {phone}
              </a>
            </ContactItem>
          ))}
          {github && (
            <li>
              <ExternalLink href={github} label="GitHub" />
            </li>
          )}
          {linkedin && (
            <li>
              <ExternalLink href={linkedin} label="LinkedIn" />
            </li>
          )}
          {years !== null && (
            <ContactItem icon={<BriefcaseIcon className="size-3.5" aria-hidden="true" />}>
              <span className="tabular-nums">{formatYears(years)} of experience</span>
            </ContactItem>
          )}
          {emails.length === 0 && phones.length === 0 && <li>No contact details</li>}
        </ul>
      </div>

      {selected && (
        <div className="flex flex-wrap items-end gap-x-6 gap-y-3 rounded-lg border bg-muted/30 px-4 py-3">
          <div className="flex min-w-0 flex-col gap-1">
            <label htmlFor={switcherId} className="text-xs text-muted-foreground">
              Application{person.applications.length > 1 && ` (${person.applications.length})`}
            </label>
            <Select value={selected.id} onValueChange={onSelect}>
              <SelectTrigger id={switcherId} size="sm" className="max-w-full min-w-64">
                <SelectValue />
              </SelectTrigger>
              <SelectContent position="popper" align="start">
                {person.applications.map((application) => (
                  <SelectItem key={application.id} value={application.id}>
                    {applicationLabel(application)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor={stageId} className="text-xs text-muted-foreground">
              Stage <Kbd aria-hidden="true">S</Kbd>
            </label>
            <ApplicationStageSelect
              id={stageId}
              target={{ applicationId: selected.id, campaignId: selected.campaign.id, personId: person.id }}
              stage={selected.pipeline_stage}
              triggerRef={stageTrigger}
            />
          </div>
        </div>
      )}
    </header>
  )
}
