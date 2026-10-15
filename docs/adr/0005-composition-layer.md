# 0005 · A composition layer for endpoints that span modules
**Status:** accepted  ·  **Date:** 2026-10-15

## Context
The module layers ([ADR 0001](0001-modular-monolith.md), [spec §3](../specs/EPIC-00-platform-core.md#3-module-structure)) let a module call only modules below it: `ingestion → hiring → talent`. A few existing endpoints need data from two modules in one request:

- `GET /campaigns/{id}`, `GET /campaigns/{id}/stats` and `GET /stats` combine hiring's candidate numbers with ingestion's resume counts.
- `DELETE /candidates/{id}` and `DELETE /campaigns/{id}/candidates/bulk` delete hiring's candidates *and* ingestion's resumes, in one transaction.

They are hiring endpoints by URL, but hiring may not call ingestion. Until E0-10 hiring simply queried the `resumes` table, which [ADR 0003](0003-no-cross-module-foreign-keys.md) and the module contracts forbid.

## Decision
Endpoints whose use case spans modules live in a thin **composition layer**, `backend/app/api/`, outside `app.modules`. It calls each module's public `service` (and uses its `schemas`), owns the transaction when more than one module writes, and holds no business rules: each module keeps its own rules and returns plain data (e.g. hiring's stats functions take the resume counts as an argument; hiring's delete functions return the affected resume ids and leave the commit to the caller). The endpoints keep their paths, tags, function names and response models, so the API does not change.

import-linter enforces it: `app.api` may import only modules' `service` and `schemas`, and neither `app.modules` nor `app.platform` may import `app.api`.

## Alternatives considered
- **Serve these endpoints from `ingestion`** (the top layer, which may call hiring): works for the dependency rule, but puts campaign stats and candidate deletes into the upload module, which owns neither, and the next cross-cutting use case (e.g. talent + hiring) would have nowhere natural to go.
- **Events** (hiring publishes "candidate deleted", ingestion deletes the resume): the right shape once modules are deployed separately, but in one process it adds machinery and loses the single transaction for no benefit today.
- **Let hiring query `resumes` directly:** the status quo before E0-10; it couples hiring to ingestion's table and breaks ADR 0003.

## Consequences
- **Easier:** module services stay single-purpose and testable on their own; the cross-module use cases are listed in one small place.
- **Harder:** a request now crosses two layers (composition → service); the composition endpoints must keep the published contract by hand (the characterization snapshots check it).
- **Watch for:** business logic creeping into `app.api`. If a composed endpoint needs a rule, the rule belongs in a module's service. When the cross-module foreign keys go (Person/Application split), deleting a campaign must move here too, so ingestion deletes the campaign's resumes explicitly instead of relying on `ON DELETE CASCADE`.
