# 0004 · Identity matching on exact normalised email or phone
**Status:** accepted  ·  **Date:** 2026-10-13

## Context
With one Person per human ([ADR 0002](0002-person-application-split.md)), every uploaded resume must be matched to an existing Person or create a new one. The same human writes contact details differently across resumes (`A.Sharma@Gmail.com` vs `asharma+jobs@gmail.com`, `+91 98765 43210` vs `09876543210`). Errors are not symmetric: a missed match leaves a duplicate that can be fixed later, while a **wrong merge** shows one person's data under another's name, which is a data-protection incident. Several workers resolve people in parallel, so two uploads for the same human can race.

## Decision
Match on an **exact** normalised email **or** phone ([spec §5.2–5.4](../specs/EPIC-00-platform-core.md#52-normalisation-talentidentitypy-pure-functions)):

- **Email:** trim and lowercase. For `gmail.com`/`googlemail.com`, remove dots and `+tag` and use `gmail.com`. For an allowlist of `+tag` providers (`outlook.com`, `hotmail.com`, `live.com`, `icloud.com`), remove `+tag` only. Other domains are left untouched. Invalid addresses are discarded.
- **Phone:** parse with `phonenumbers` using a configurable `PHONE_DEFAULT_REGION` (default `IN`) and store as E.164. Unparseable numbers are discarded.
- **Rules:** no match → new Person. All matches point to one Person → attach to it. Email matches A but phone matches B → attach to the **email** match and record an `identity_conflicts` row; **never merge automatically**. No usable identifiers → new Person.
- **Storage:** identifiers live in a separate `person_identifiers` table with **UNIQUE(kind, value_normalized)**, which becomes `(agency_id, kind, value_normalized)` in E2.
- **Concurrency:** `resolve_person` runs in one transaction and first takes a `pg_advisory_xact_lock` per normalised identifier, in sorted order to avoid deadlocks. A unique violation that still slips through rolls back and retries once.

## Alternatives considered
- **Exact email only:** simpler, but misses people who change email and keep the same phone, which is common.
- **Fuzzy or ML matching with automatic merge** (name similarity, embeddings): catches more duplicates, but a wrong merge is a data-protection incident and hard to undo. Rejected.
- **A human review queue for possible duplicates now:** the right long-term answer, but it needs UI and search we don't have yet. Deferred to E6; E0 only records conflicts for it to consume.

## Consequences
- **Easier:** matching is deterministic, explainable and testable with pure functions and property tests. The unique constraint plus advisory locks guarantee one Person per identifier even under parallel uploads.
- **Harder:** some true duplicates (different email and phone, or no identifiers) stay separate People until the E6 review queue exists.
- **Watch for:** normalisation rules must stay conservative and well tested, because a rule that is too aggressive turns two people into one. Add providers to the `+tag` allowlist only with evidence, and monitor the volume of `identity_conflicts`.
