"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { Loader2Icon } from "lucide-react"
import { useForm } from "react-hook-form"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Form } from "@/components/ui/form"

import { useCreateCampaign, type ParsedJobDescription } from "../api"
import { CampaignFields, FormServerError } from "../components/campaign-fields"
import { applyServerErrors } from "../components/server-errors"
import {
  campaignFormSchema,
  DESCRIPTION_MAX,
  EMPTY_CAMPAIGN_FORM,
  formValuesToRequest,
  mergeSkills,
  type CampaignFormInput,
  type CampaignFormValues,
} from "../schema"
import { JobDescriptionExtractor } from "./job-description-extractor"

const FIELDS = ["title", "role", "description", "required_skills", "status"] as const

/** `/campaigns/new`: create a campaign, optionally starting from a pasted job description. */
export function NewCampaignForm() {
  const router = useRouter()
  const create = useCreateCampaign()
  const form = useForm<CampaignFormInput, unknown, CampaignFormValues>({
    resolver: zodResolver(campaignFormSchema),
    defaultValues: EMPTY_CAMPAIGN_FORM,
  })

  /** Fills the form from the AI's reading of a JD; returns what changed, for the panel's status line. */
  function applyExtraction(parsed: ParsedJobDescription, jobDescription: string): string {
    const options = { shouldDirty: true, shouldValidate: true } as const
    const changes: string[] = []
    if (parsed.role_title.trim()) {
      form.setValue("role", parsed.role_title.trim(), options)
      changes.push("the role")
    }
    const current = form.getValues("required_skills")
    const skills = mergeSkills(current, parsed.skills)
    const added = skills.length - current.length
    if (added > 0) {
      form.setValue("required_skills", skills, options)
      changes.push(`${added} ${added === 1 ? "skill" : "skills"}`)
    }
    // The JD doubles as the description when there is none yet and it fits.
    if (!form.getValues("description").trim() && jobDescription.length <= DESCRIPTION_MAX) {
      form.setValue("description", jobDescription, options)
      changes.push("the description")
    }
    const seniority = parsed.seniority ? ` Seniority: ${parsed.seniority}.` : ""
    if (changes.length === 0) return `Nothing new found in the job description.${seniority}`
    return `Filled in ${joinList(changes)}.${seniority} Review them below.`
  }

  function onSubmit(values: CampaignFormValues) {
    create.mutate(formValuesToRequest(values), {
      onSuccess: (campaign) => {
        toast.success("Campaign created", { description: campaign.title })
        router.push(`/campaigns/${campaign.id}`)
      },
      onError: (error) => applyServerErrors(error, form.setError, FIELDS),
    })
  }

  const busy = create.isPending || create.isSuccess

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-6 py-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">New campaign</h1>
        <p className="text-muted-foreground">
          One campaign per opening. Upload resumes to it next, and they are scored against the role and skills.
        </p>
      </header>

      <JobDescriptionExtractor onExtracted={applyExtraction} disabled={busy} />

      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} noValidate aria-label="New campaign" className="flex flex-col gap-6">
          <CampaignFields disabled={busy} />
          <FormServerError message={form.formState.errors.root?.server?.message} />
          <div className="flex items-center justify-end gap-2 border-t pt-4">
            <Button variant="ghost" asChild>
              <Link href="/campaigns">Cancel</Link>
            </Button>
            <Button type="submit" disabled={busy}>
              {busy && <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />}
              {busy ? "Creating…" : "Create campaign"}
            </Button>
          </div>
        </form>
      </Form>
    </div>
  )
}

function joinList(items: string[]): string {
  if (items.length <= 1) return items.join("")
  return `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`
}
