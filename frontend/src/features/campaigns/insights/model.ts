import { CATEGORIES, CATEGORY_META, type Category } from "@/lib/applications"

import type { CampaignStats } from "../api"

/*
 * The Insights tab's data (spec §4): `GET /campaigns/{id}/stats` turned into
 * one row per bar. The charts and their text alternatives (summary and data
 * table) read the same rows, so they cannot disagree.
 */

export type CategoryRow = { key: Category | "uncategorised"; label: string; count: number; fill: string }

/**
 * Applications per category, best first, plus the ones without a category
 * (not scored yet) when there are any. `fill` is the bar's colour, a chart
 * config variable that resolves to a `--category-*` token.
 */
export function categoryRows(stats: CampaignStats): CategoryRow[] {
  const { total, by_category: byCategory } = stats.applications
  const rows: CategoryRow[] = CATEGORIES.map((category) => ({
    key: category,
    label: CATEGORY_META[category].label,
    count: byCategory[category],
    fill: `var(--color-${category})`,
  }))
  const uncategorised = total - rows.reduce((sum, row) => sum + row.count, 0)
  if (uncategorised > 0) {
    rows.push({ key: "uncategorised", label: "Not scored", count: uncategorised, fill: "var(--color-uncategorised)" })
  }
  return rows
}

export type ScoreBucketRow = { label: string; min: number; max: number; count: number }

/** The score histogram: ten buckets of ten, "0–9" … "90–100" (the last one includes 100). */
export function scoreBucketRows(stats: CampaignStats): ScoreBucketRow[] {
  const buckets = [...stats.score_histogram].sort((a, b) => a.min - b.min)
  return buckets.map((bucket, index) => ({
    label: index === buckets.length - 1 ? `${bucket.min}–${bucket.max}` : `${bucket.min}–${bucket.max - 1}`,
    min: bucket.min,
    max: bucket.max,
    count: bucket.count,
  }))
}

export type SkillRow = { skill: string; count: number; required: boolean; fill: string }

/** The most common skills among the applicants, most common first; required ones are highlighted. */
export function skillRows(stats: CampaignStats): SkillRow[] {
  return stats.top_skills.map(({ skill, count, required }) => ({
    skill,
    count,
    required,
    fill: required ? "var(--color-required)" : "var(--color-other)",
  }))
}

/**
 * Required skills that are not among the most common ones. The stats list
 * only the top skills, so a skill here may still be on a few resumes.
 */
export function requiredSkillsNotInTop(requiredSkills: readonly string[], stats: CampaignStats): string[] {
  const top = new Set(stats.top_skills.map(({ skill }) => skill.toLowerCase()))
  return requiredSkills.filter((skill) => !top.has(skill.toLowerCase()))
}

export type FunnelRow = { key: "uploaded" | "processed" | "scored" | "reviewed"; label: string; count: number }

/**
 * From resume to reviewed candidate: resumes uploaded, processed (done),
 * scored applications, and applications a recruiter has moved to a stage.
 */
export function funnelRows(stats: CampaignStats): FunnelRow[] {
  const { resumes, applications } = stats
  return [
    { key: "uploaded", label: "Uploaded", count: resumes.total },
    { key: "processed", label: "Processed", count: resumes.done },
    { key: "scored", label: "Scored", count: applications.total },
    { key: "reviewed", label: "Reviewed", count: Math.max(0, applications.total - applications.by_stage.unreviewed) },
  ]
}

export type ResumeStatusRow = { key: string; label: string; count: number }

/** Resumes not (or not yet) processed, by status: what the funnel's first step loses. */
export function unfinishedResumeRows(stats: CampaignStats): ResumeStatusRow[] {
  const { resumes } = stats
  return [
    { key: "pending", label: "Queued", count: resumes.pending },
    { key: "extracting", label: "Reading", count: resumes.extracting },
    { key: "processing", label: "Scoring", count: resumes.processing },
    { key: "failed", label: "Failed", count: resumes.failed },
    { key: "quota_exceeded", label: "Quota limit", count: resumes.quota_exceeded },
  ]
}

/** "4 (44%)": a count and its share of `total`, for summaries and tables. */
export function withShare(count: number, total: number): string {
  if (total <= 0) return String(count)
  return `${count} (${Math.round((count / total) * 100)}%)`
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`
}
