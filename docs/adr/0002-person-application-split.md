# 0002 · Split Candidate into Person and Application
**Status:** accepted  ·  **Date:** 2026-10-13

## Context
Today a `Candidate` is one row per uploaded resume per campaign: the facts about the human (name, contacts, skills, experience) and the judgement against one role (score, category, strengths, pipeline stage, notes) live together. Someone who applies to three campaigns becomes three unrelated candidates. Our target buyer is recruitment agencies, whose core value is **talent rediscovery**: finding people they already know when a new role opens. That needs one record per human that persists across campaigns, while the judgement must stay per campaign, because the same person can score 90 for one role and 40 for another.

## Decision
Replace `Candidate` with two entities owned by different modules ([spec §4](../specs/EPIC-00-platform-core.md#4-data-model)):

- **Person** (`talent`): facts about the human. One record per human, with the profile taken from the most recently uploaded resume and identifiers held in `person_identifiers` (see [ADR 0004](0004-identity-matching-policy.md)).
- **Application** (`hiring`): that person's application to one campaign: the resume submitted, score, score breakdown, category, summary, strengths, weaknesses, missing skills, recommendation, pipeline stage and notes. **UNIQUE(campaign_id, person_id)**, so a re-application to the same campaign updates the existing Application.

## Alternatives considered
- **Keep `Candidate` and add a `person_key` column or a grouping view:** cheapest change, but the profile stays duplicated on every row, "the person" is a derived concept with no place to hold identifiers or conflicts, and every later epic (search, tenancy, agents) would build on the wrong model.
- **Person only, with per-campaign scores in a JSON map:** one table, but pipeline stage, notes and scoring become untyped, unindexable blobs, and the hiring module would have to write into a talent-owned table.
- **Full event-sourced history of people and applications:** gives a complete audit trail, but is far more machinery than the problem needs now. Audit requirements arrive with E7 and can be met then.

## Consequences
- **Easier:** talent rediscovery and search across campaigns (E6), a clear owner for each piece of data, and a natural place for identity matching.
- **Harder:** the change needs a zero-downtime **expand → backfill → verify → switch → contract** migration with dual writes ([spec §6](../specs/EPIC-00-platform-core.md#6-migration-plan-expand--backfill--contract)). IDs must stay stable: applications reuse the legacy `candidates.id`, and IDs of candidates merged into another application are redirected through `legacy_candidate_map`. The existing frontend is served by a compatibility layer that rebuilds the legacy response shapes ([spec §7](../specs/EPIC-00-platform-core.md#7-api-compatibility-layer)) until E1.
- **Watch for:** drift between the dual-write path and the backfill (both must call the same `resolve_person` and `upsert_application` services), and the `candidates` table lingering after the switch. It is dropped after one full sprint without issues.
