"use client"

import { ChartColumnIcon, UploadIcon } from "lucide-react"
import { Bar, BarChart, CartesianGrid, LabelList, XAxis, YAxis } from "recharts"

import { EmptyState, ErrorState } from "@/components/data-states"
import { Button } from "@/components/ui/button"
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

import { useCampaignStats, type Campaign, type CampaignStats } from "../api"
import { ChartPanel } from "./chart-panel"
import {
  categoryRows,
  funnelRows,
  plural,
  requiredSkillsNotInTop,
  scoreBucketRows,
  skillRows,
  unfinishedResumeRows,
  withShare,
} from "./model"

/*
 * Colours are design tokens (EPIC-01 §3): the chart wrapper turns each config
 * entry into a `--color-<key>` variable on the chart, and bars fill with
 * `var(--color-<key>)`. Categories use their `--category-*` tokens; the rest
 * the teal chart ramp and its neutral (`--chart-1`, `--chart-4`), so dark
 * mode follows the theme with no extra code.
 */
const CATEGORY_CONFIG = {
  count: { label: "Applications" },
  strong_match: { label: "Strong match", color: "var(--category-strong)" },
  moderate_match: { label: "Moderate match", color: "var(--category-moderate)" },
  weak_match: { label: "Weak match", color: "var(--category-weak)" },
  rejected: { label: "Rejected", color: "var(--category-rejected)" },
  uncategorised: { label: "Not scored", color: "var(--muted-foreground)" },
} satisfies ChartConfig

const HISTOGRAM_CONFIG = {
  count: { label: "Applications", color: "var(--chart-1)" },
} satisfies ChartConfig

const SKILLS_CONFIG = {
  count: { label: "Applicants" },
  required: { label: "Required", color: "var(--chart-1)" },
  other: { label: "Not required", color: "var(--chart-4)" },
} satisfies ChartConfig

const FUNNEL_CONFIG = {
  count: { label: "Count", color: "var(--chart-1)" },
} satisfies ChartConfig

const AXIS_TICK = { fontSize: 12 }
/** Horizontal bars: rounded at the data end only (4 px). */
const BAR_END_RADIUS: [number, number, number, number] = [0, 4, 4, 0]

/**
 * The workspace's Insights tab (spec §4): category distribution, score
 * histogram, top skills against the required ones and the processing funnel,
 * from `GET /campaigns/{id}/stats`. The workspace's live events invalidate
 * the stats as resumes finish, so the charts follow uploads without polling.
 */
export function InsightsTab({ campaign, onUpload }: { campaign: Campaign; onUpload: () => void }) {
  const stats = useCampaignStats(campaign.id)

  if (stats.isPending) return <InsightsSkeleton />

  if (stats.isError) {
    return <ErrorState error={stats.error} onRetry={() => void stats.refetch()} retrying={stats.isFetching} />
  }

  const data = stats.data
  if (data.resumes.total === 0 && data.applications.total === 0) {
    return (
      <EmptyState
        icon={ChartColumnIcon}
        title="No insights yet"
        description="Upload resumes to this campaign: as they are scored, you'll see how the candidates compare."
        action={
          <Button onClick={onUpload}>
            <UploadIcon data-icon="inline-start" />
            Upload resumes
          </Button>
        }
      />
    )
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <CategoryChart stats={data} />
      <ScoreHistogram stats={data} />
      <TopSkillsChart stats={data} requiredSkills={campaign.required_skills} />
      <ProcessingFunnel stats={data} />
    </div>
  )
}

function CategoryChart({ stats }: { stats: CampaignStats }) {
  const rows = categoryRows(stats)
  const total = stats.applications.total
  const parts = rows.map((row) => `${row.count} ${row.label.toLowerCase()}`)
  const summary =
    total === 0
      ? "Applications by the AI's match category."
      : `${plural(total, "application")}: ${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}.`

  return (
    <ChartPanel
      title="Category distribution"
      summary={summary}
      empty={total === 0 ? "No scored applications yet." : undefined}
      table={{
        columns: [{ header: "Category" }, { header: "Applications", numeric: true }],
        rows: rows.map((row) => ({ key: row.key, cells: [row.label, withShare(row.count, total)] })),
      }}
    >
      <ChartContainer config={CATEGORY_CONFIG} className="aspect-auto h-48 w-full">
        <BarChart data={rows} layout="vertical" margin={{ left: 0, right: 32 }} accessibilityLayer={false}>
          <XAxis type="number" dataKey="count" hide allowDecimals={false} />
          <YAxis type="category" dataKey="label" width={112} tickLine={false} axisLine={false} tick={AXIS_TICK} />
          <ChartTooltip cursor={false} content={<ChartTooltipContent indicator="dot" />} />
          <Bar dataKey="count" radius={BAR_END_RADIUS} maxBarSize={24} isAnimationActive={false}>
            <LabelList dataKey="count" position="right" className="fill-foreground" fontSize={12} />
          </Bar>
        </BarChart>
      </ChartContainer>
    </ChartPanel>
  )
}

function ScoreHistogram({ stats }: { stats: CampaignStats }) {
  const rows = scoreBucketRows(stats)
  const scored = rows.reduce((sum, row) => sum + row.count, 0)
  const busiest = rows.reduce<(typeof rows)[number] | undefined>(
    (best, row) => (row.count > (best?.count ?? 0) ? row : best),
    undefined,
  )
  const average = stats.avg_score === null ? null : Math.round(stats.avg_score)
  const summary =
    scored === 0
      ? "How the applications' scores (0–100) are spread."
      : [
          `${plural(scored, "scored application")}`,
          average !== null ? `, average score ${average}` : "",
          busiest ? `. Most score ${busiest.label} (${busiest.count}).` : ".",
        ].join("")

  return (
    <ChartPanel
      title="Score histogram"
      summary={summary}
      empty={scored === 0 ? "No scores yet." : undefined}
      table={{
        columns: [{ header: "Score" }, { header: "Applications", numeric: true }],
        rows: rows.map((row) => ({ key: row.label, cells: [row.label, row.count] })),
      }}
    >
      <ChartContainer config={HISTOGRAM_CONFIG} className="aspect-auto h-48 w-full">
        <BarChart data={rows} margin={{ left: -8, right: 4, top: 8 }} barCategoryGap={2} accessibilityLayer={false}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fontSize: 11 }} interval={0} />
          <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={32} tick={AXIS_TICK} />
          <ChartTooltip
            cursor={false}
            content={<ChartTooltipContent labelFormatter={(label) => `Score ${String(label)}`} />}
          />
          <Bar dataKey="count" fill="var(--color-count)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ChartContainer>
    </ChartPanel>
  )
}

function TopSkillsChart({ stats, requiredSkills }: { stats: CampaignStats; requiredSkills: string[] }) {
  const rows = skillRows(stats)
  const required = rows.filter((row) => row.required)
  const missing = requiredSkillsNotInTop(requiredSkills, stats)
  const summary =
    rows.length === 0
      ? "The skills found most often on the resumes, with the required ones highlighted."
      : `The ${plural(rows.length, "most common skill")} on the resumes. ` +
        (required.length === 0
          ? "None of them is a required skill."
          : `Required: ${required.map((row) => `${row.skill} (${row.count})`).join(", ")}.`)

  return (
    <ChartPanel
      title="Top skills"
      summary={summary}
      empty={rows.length === 0 ? "No skills extracted yet." : undefined}
      table={{
        columns: [{ header: "Skill" }, { header: "Required" }, { header: "Applicants", numeric: true }],
        rows: rows.map((row) => ({ key: row.skill, cells: [row.skill, row.required ? "Yes" : "No", row.count] })),
      }}
      footer={
        <div className="flex flex-col gap-2 text-xs text-muted-foreground">
          {rows.length > 0 && (
            <ul className="flex items-center gap-4" aria-label="Legend">
              <LegendItem swatchClassName="bg-chart-1" label="Required" />
              <LegendItem swatchClassName="bg-chart-4" label="Not required" />
            </ul>
          )}
          {missing.length > 0 && (
            <p>
              Required but not among the most common: <span className="text-foreground">{missing.join(", ")}</span>
            </p>
          )}
        </div>
      }
    >
      <ChartContainer config={SKILLS_CONFIG} className="aspect-auto h-72 w-full">
        <BarChart data={rows} layout="vertical" margin={{ left: 0, right: 32 }} accessibilityLayer={false}>
          <XAxis type="number" dataKey="count" hide allowDecimals={false} />
          <YAxis type="category" dataKey="skill" width={112} tickLine={false} axisLine={false} tick={AXIS_TICK} />
          <ChartTooltip cursor={false} content={<ChartTooltipContent indicator="dot" />} />
          <Bar dataKey="count" radius={BAR_END_RADIUS} maxBarSize={20} isAnimationActive={false}>
            <LabelList dataKey="count" position="right" className="fill-foreground" fontSize={12} />
          </Bar>
        </BarChart>
      </ChartContainer>
    </ChartPanel>
  )
}

function LegendItem({ swatchClassName, label }: { swatchClassName: string; label: string }) {
  return (
    <li className="flex items-center gap-1.5">
      <span aria-hidden="true" className={cn("size-2 rounded-[2px]", swatchClassName)} />
      {label}
    </li>
  )
}

function ProcessingFunnel({ stats }: { stats: CampaignStats }) {
  const uploaded = stats.resumes.total
  // Each step's share of the uploads (the first step is the whole).
  const rows = funnelRows(stats).map((row, index) => ({
    ...row,
    display: index === 0 ? String(row.count) : withShare(row.count, uploaded),
  }))
  const unfinished = unfinishedResumeRows(stats).filter((row) => row.count > 0)
  const summary = `${rows.map((row) => `${row.count} ${row.label.toLowerCase()}`).join(", ")}.`

  return (
    <ChartPanel
      title="Processing funnel"
      summary={summary}
      table={{
        columns: [{ header: "Step" }, { header: "Count", numeric: true }],
        rows: [
          ...rows.map((row) => ({ key: row.key, cells: [row.label, row.display] })),
          ...unfinished.map((row) => ({ key: row.key, cells: [`Resumes: ${row.label.toLowerCase()}`, row.count] })),
        ],
      }}
      footer={
        <p className="text-xs text-muted-foreground">
          {unfinished.length === 0
            ? "Every uploaded resume has been processed."
            : `Not processed yet: ${unfinished.map((row) => `${row.count} ${row.label.toLowerCase()}`).join(", ")}.`}
        </p>
      }
    >
      <ChartContainer config={FUNNEL_CONFIG} className="aspect-auto h-48 w-full">
        <BarChart data={rows} layout="vertical" margin={{ left: 0, right: 64 }} accessibilityLayer={false}>
          <XAxis type="number" dataKey="count" hide allowDecimals={false} domain={[0, "dataMax"]} />
          <YAxis type="category" dataKey="label" width={80} tickLine={false} axisLine={false} tick={AXIS_TICK} />
          <ChartTooltip cursor={false} content={<ChartTooltipContent hideIndicator />} />
          <Bar dataKey="count" fill="var(--color-count)" radius={BAR_END_RADIUS} maxBarSize={24} isAnimationActive={false}>
            <LabelList dataKey="display" position="right" className="fill-foreground" fontSize={12} />
          </Bar>
        </BarChart>
      </ChartContainer>
    </ChartPanel>
  )
}

function InsightsSkeleton() {
  return (
    <div className="grid gap-4 lg:grid-cols-2" aria-busy="true" aria-label="Loading insights">
      {Array.from({ length: 4 }, (_, index) => (
        <div key={index} className="flex flex-col gap-3 rounded-lg border p-4">
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-3.5 w-64" />
          <Skeleton className="h-44 w-full" />
        </div>
      ))}
    </div>
  )
}
