# 0004 · Identity matching on exact normalised email or phone
**Status:** accepted  ·  **Date:** 2026-10-13

## Context
With one Person per human ([ADR 0002](0002-person-application-split.md)), every uploaded resume must be matched to an existing Person or create a new one. The same human writes contact details differently across resumes (`A.Sharma@Gmail.com` vs `asharma+jobs@gmail.com`, `+91 98765 43210` vs `09876543210`). Errors are not symmetric: a missed match leaves a duplicate that can be fixed later, while a **wrong merge** shows one person's data under another's name, which is a data-protection incident. Several workers resolve people in parallel, so two uploads for the same human can race.

## Decision
Match on an **exact** normalised email **or** phone ([spec §5.2–5.4](../specs/EPIC-00-platform-core.md#52-normalisation-talentidentitypy-pure-functions)):

- **Email:** trim and lowercase. For `gmail.com`/`googlemail.com`, remove dots and `+tag` and use `gmail.com`. For an allowlist of `+tag` providers (`outlook.com`, `hotmail.com`, `live.com`, `icloud.com`), remove `+tag` only. Other domains are left untouched. Invalid addresses are discarded.
- **Phone:** parse with `phonenumbers` using a configurable `PHONE_DEFAULT_REGION` (default `IN`) and store as E.164. Unparseable numbers are discarded. Only phones written with a country code, or that are mobile numbers, match on their own; see the [2026-10-16 amendment](#amendment-2026-10-16-phone-numbers-without-a-country-code).
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

## Amendment (2026-10-16): phone numbers without a country code
**Status:** accepted  ·  **Ticket:** E0-15  ·  **Decided in:** [Sprint 3](../sprints/sprint-03.md#decision-needed-before-e0-15-phone-numbers-without-a-country-code)

### Context
E0-11 found that with `PHONE_DEFAULT_REGION=IN`, a US number written as `(415) 555-2671` normalises to `+914155552671`, which is a **valid Indian landline**. `phonenumbers` cannot tell that the writer meant another country: a national-format number is read in the default region, and many foreign numbers are also valid numbers there. Matching on such a value could attach one person's resume to a different human, the wrong merge this ADR exists to prevent. The risk comes from the region being *inferred*, and is concentrated in landlines: Indian mobile numbers have a distinctive shape (ten digits starting 6–9), and resumes almost always list a mobile.

### Decision
**Option A.** A phone may match a Person on its own only if it is **match-eligible** (`talent.identity.classify_phone`):

- it was written **with an explicit country code**: `+91 …` or an international dialling prefix such as `0091 …` (`phonenumbers` `country_code_source` is `FROM_NUMBER_WITH_PLUS_SIGN` or `FROM_NUMBER_WITH_IDD`; digits that `phonenumbers` merely guesses to be the default region's code do not count), **or**
- it parses as a **mobile** number in the default region (`MOBILE`, or `FIXED_LINE_OR_MOBILE` for regions such as US/CA where the two are indistinguishable).

Any other valid phone (a landline whose country was only inferred) is stored in `person_identifiers` with **`inferred = true`**. An inferred phone:

- never causes a match and never causes an `identity_conflicts` row, in either direction: an inferred phone on a new resume is not looked up, and a stored inferred row is not matched even by an explicitly written number;
- is still stored for the Person the resume resolved to, so the E6 review queue can use it as a hint;
- is **not stored** when the same value already belongs to a different Person (`UNIQUE(kind, value_normalized)` allows one owner); `resolve_person` logs `identifier_owned_by_other_person` at info level and carries on;
- is **confirmed** (`inferred` set to false) only when the Person that owns the row is matched on another identifier (the email) and the new resume writes the same number in a match-eligible way. Nothing else upgrades it: in particular an explicit number never takes over another Person's inferred row.

### Alternatives considered
- **B: match on every valid normalised phone** (the policy as first written). Simplest, but keeps the known cross-country false merge.
- **C: match on email only; store phones but never match on them.** Safest, but misses people who change email and keep their phone, which is common; it would throw away the many correct Indian mobile matches to remove a risk that Option A already removes.

### Consequences
- **Easier:** the cross-country landline merge is gone, while Indian mobiles, the large majority of phones on resumes, still match however they are written (`98765 43210`, `09876543210`, `+91 98765 43210`).
- **Harder:** two resumes from the same human that share only a landline written without a country code (and no email) produce two People. They stay separate until the E6 review queue, where the shared inferred number is a strong hint.
- **Schema:** one extra column, `person_identifiers.inferred boolean NOT NULL DEFAULT false` (E0-14).
- **Watch for:** a foreign *mobile* written without a country code that is also a valid Indian mobile still matches as Indian. That needs a ten-digit number starting 6–9, which is uncommon outside India but possible; monitor `identity_conflicts` and reconsider if E2 brings agencies in other regions (the default region then becomes per agency). Changing the classification later only changes future matches: stored `inferred` flags are not recomputed.
