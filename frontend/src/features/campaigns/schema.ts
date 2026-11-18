import { z } from "zod"

import type { Schema } from "@/lib/api/client"

import type { Campaign, CampaignCreate, CampaignUpdate } from "./api"

export type CampaignStatus = Schema<"CampaignStatus">

/** In the order the UI lists them (the API's `CampaignStatus` enum). */
export const CAMPAIGN_STATUSES = ["active", "closed", "archived"] as const satisfies readonly CampaignStatus[]

export const CAMPAIGN_STATUS_LABELS: Record<CampaignStatus, string> = {
  active: "Active",
  closed: "Closed",
  archived: "Archived",
}

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
  title: "",
  role: "",
  description: "",
  required_skills: [],
  status: "active",
}

export function campaignToFormValues(campaign: Campaign): CampaignFormInput {
  return {
    title: campaign.title,
    role: campaign.role,
    description: campaign.description ?? "",
    required_skills: campaign.required_skills,
    status: campaign.status,
  }
}

/** The request body. An empty description is sent as `null` (no description). */
export function formValuesToRequest(values: CampaignFormValues): CampaignCreate & CampaignUpdate {
  const description = values.description.trim()
  return {
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
