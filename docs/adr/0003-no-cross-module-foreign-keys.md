# 0003 · No foreign keys across module boundaries
**Status:** accepted  ·  **Date:** 2026-10-13

## Context
[ADR 0001](0001-modular-monolith.md) splits the backend into modules that each own their tables and talk only through service interfaces. The Person/Application split ([ADR 0002](0002-person-application-split.md)) creates references that cross those boundaries: `applications.person_id` (hiring → talent), `applications.resume_id` (hiring → ingestion), `people.latest_resume_id` (talent → ingestion) and `resumes.person_id` (ingestion → talent). Code boundaries alone are not enough if the database schema couples the modules anyway.

## Decision
References **between** modules are stored as plain UUIDs **without** foreign key constraints. Foreign keys are used only **within** a module, e.g. `person_identifiers.person_id → people` and `applications.campaign_id → campaigns` ([spec §4, "Cross-module references"](../specs/EPIC-00-platform-core.md#cross-module-references)). Consistency across modules is the job of the service layer and is checked by the backfill verification script ([spec §6.3](../specs/EPIC-00-platform-core.md#63-verification-checks)).

## Alternatives considered
- **Foreign keys everywhere:** the simplest way to guarantee integrity, but it couples the modules' schemas so that no module can be migrated, moved or extracted on its own, and cascading deletes would cross module boundaries implicitly, bypassing the owning module's service.
- **A separate Postgres schema per module:** makes ownership visible in the database and allows per-module permissions. It adds migration and tooling overhead we don't need yet, so we keep it as a possible future step; this decision does not rule it out.

## Consequences
- **Easier:** each module's tables can change, or move to another database, without touching the others, which keeps the extraction option from ADR 0001 open.
- **Harder:** the database no longer stops dangling references. Deletes that affect another module must be explicit service calls, never cascades, and detecting orphans is our job.
- **Watch for:** orphaned `person_id` and `resume_id` values. The verification checks run during the migration; if orphans appear in normal operation, promote them to a scheduled consistency check.
