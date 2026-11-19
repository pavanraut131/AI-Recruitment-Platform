import { describe, expect, it } from "vitest"

import { statsFixture } from "../test-utils"
import { categoryRows, funnelRows, requiredSkillsNotInTop, scoreBucketRows, skillRows, withShare } from "./model"

describe("Insights model", () => {
  it("fills each category's bar with its token variable, and counts unscored applications", () => {
    const stats = statsFixture({
      applications: { ...statsFixture().applications, total: 11 },
    })
    expect(categoryRows(stats).map(({ key, count, fill }) => [key, count, fill])).toEqual([
      ["strong_match", 4, "var(--color-strong_match)"],
      ["moderate_match", 3, "var(--color-moderate_match)"],
      ["weak_match", 1, "var(--color-weak_match)"],
      ["rejected", 1, "var(--color-rejected)"],
      ["uncategorised", 2, "var(--color-uncategorised)"],
    ])
  })

  it("labels score buckets in order, the last one including 100", () => {
    const stats = statsFixture({
      score_histogram: [
        { min: 90, max: 100, count: 1 },
        { min: 0, max: 10, count: 2 },
        { min: 10, max: 20, count: 0 },
      ],
    })
    expect(scoreBucketRows(stats).map(({ label, count }) => [label, count])).toEqual([
      ["0–9", 2],
      ["10–19", 0],
      ["90–100", 1],
    ])
  })

  it("highlights required skills and finds required ones outside the top list (case-insensitively)", () => {
    const stats = statsFixture({
      top_skills: [
        { skill: "python", count: 3, required: true },
        { skill: "Go", count: 2, required: false },
      ],
    })
    expect(skillRows(stats).map(({ skill, fill }) => [skill, fill])).toEqual([
      ["python", "var(--color-required)"],
      ["Go", "var(--color-other)"],
    ])
    expect(requiredSkillsNotInTop(["Python", "SQL"], stats)).toEqual(["SQL"])
  })

  it("builds the funnel from resume and application counts", () => {
    const stats = statsFixture({
      applications: {
        ...statsFixture().applications,
        by_stage: { ...statsFixture().applications.by_stage, unreviewed: 6, screened: 3 },
      },
    })
    expect(funnelRows(stats).map(({ label, count }) => [label, count])).toEqual([
      ["Uploaded", 12],
      ["Processed", 9],
      ["Scored", 9],
      ["Reviewed", 3],
    ])
    expect(withShare(3, 12)).toBe("3 (25%)")
    expect(withShare(0, 0)).toBe("0")
  })
})
