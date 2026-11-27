import { z } from "zod"

import { CAMPAIGN_STATUSES, type CampaignStatus } from "@/lib/campaigns"

import type { Campaign, CampaignCreate, CampaignUpdate } from "./api"

export { CAMPAIGN_STATUS_LABELS, CAMPAIGN_STATUSES, type CampaignStatus } from "@/lib/campaigns"

export const CAMPAIGN_STATUS_DESCRIPTIONS: Record<CampaignStatus, string> = {
  active: "Open: accepting uploads and reviewing candidates.",
  closed: "Hiring finished; candidates stay reviewable.",
  archived: "Hidden from day-to-day work; kept for history.",
}

/*
 * Limits mirror `CampaignCreate` / `CampaignUpdate` in the v2 document, so the
 * form rejects what the API would reject. The API stays the authority: a 422
 * it returns anyway is mapped onto the same fields (`applyServerErrors`).
 */
export const TITLE_MAX = 255
export const ROLE_MAX = 255
export const DESCRIPTION_MAX = 5_000
export const SKILL_MAX = 100
export const SKILLS_MAX = 50
/** `JobDescription.job_description` in the v2 document. */
export const JOB_DESCRIPTION_MAX = 50_000

export const campaignFormSchema = z.object({
  /** Required in the UI (spec §9.3), though the API would pick the user's default client. */
  client_id: z.string().min(1, "Choose the client this campaign is for."),
  title: z
    .string()
    .trim()
    .min(1, "Give the campaign a title.")
    .max(TITLE_MAX, `Keep the title under ${TITLE_MAX} characters.`),
  role: z
    .string()
    .trim()
    .min(1, "Enter the role you are hiring for.")
    .max(ROLE_MAX, `Keep the role under ${ROLE_MAX} characters.`),
  description: z.string().max(DESCRIPTION_MAX, `Keep the description under ${DESCRIPTION_MAX.toLocaleString("en")} characters.`),
  required_skills: z
    .array(z.string().trim().min(1).max(SKILL_MAX, `Each skill must be under ${SKILL_MAX} characters.`))
    .max(SKILLS_MAX, `Add at most ${SKILLS_MAX} skills.`),
  status: z.enum(CAMPAIGN_STATUSES),
})

/** What the form edits (zod's output: titles trimmed). */
export type CampaignFormValues = z.infer<typeof campaignFormSchema>
/** What the inputs hold before validation. */
export type CampaignFormInput = z.input<typeof campaignFormSchema>

export const EMPTY_CAMPAIGN_FORM: CampaignFormInput = {
  client_id: "",
  title: "",
  role: "",
  description: "",
  required_skills: [],
  status: "active",
}

export function campaignToFormValues(campaign: Campaign): CampaignFormInput {
  return {
    client_id: campaign.client.id,
    title: campaign.title,
    role: campaign.role,
    description: campaign.description ?? "",
    required_skills: campaign.required_skills,
    status: campaign.status,
  }
}

/**
 * The request body. An empty description is sent as `null` (no description).
 * `currentClientId` (an edit): the client is sent only when it changed, since
 * sending a campaign's archived client back would be refused.
 */
export function formValuesToRequest(
  values: CampaignFormValues,
  { currentClientId }: { currentClientId?: string } = {},
): CampaignCreate & CampaignUpdate {
  const description = values.description.trim()
  return {
    ...(values.client_id !== currentClientId && { client_id: values.client_id }),
    title: values.title,
    role: values.role,
    description: description === "" ? null : description,
    required_skills: values.required_skills,
    status: values.status,
  }
}

/** Adds skills to a list, skipping blanks and case-insensitive duplicates, keeping the existing order. */
export function mergeSkills(current: readonly string[], added: readonly string[]): string[] {
  const seen = new Set(current.map((skill) => skill.toLowerCase()))
  const merged = [...current]
  for (const raw of added) {
    const skill = raw.trim()
    if (!skill || seen.has(skill.toLowerCase())) continue
    seen.add(skill.toLowerCase())
    merged.push(skill)
  }
  return merged
}

export const jobDescriptionSchema = z
  .string()
  .trim()
  .min(1, "Paste a job description first.")
  .max(JOB_DESCRIPTION_MAX, `Job descriptions are limited to ${JOB_DESCRIPTION_MAX.toLocaleString("en")} characters.`)
