# Roadmap

**Product:** an AI hiring-intelligence layer for **recruitment agencies**. It turns every resume an agency has ever received into a searchable, explainable, reusable talent pool.

**Approach:** a modular monolith (see [ADR 0001](adr/0001-modular-monolith.md)). We build each layer properly before pitching to clients. Working process is described in [process.md](process.md).

**Tenancy model:** Agency → Clients → Campaigns. People live in the agency-wide talent pool, and each one can have Applications to many campaigns.

## Epics

Status values: ⬜ not started · 🟦 designing · 🟨 in progress · ✅ done

| # | Epic | Status | Core concepts | Spec |
|---|---|---|---|---|
| E0 | **Platform core**: modular restructure, Person/Application split, Postgres test infra, working CI | ✅ | DDD, testcontainers, data migrations | [spec](specs/EPIC-00-platform-core.md) |
| E1 | **Product design & frontend foundation (HireFlow AI)**: agency-model screen structure, design system (shadcn/ui, tokens, light/dark), TanStack Query, API client generated from OpenAPI, rebuild existing screens, Playwright | ✅ | Product/UX design, design systems, frontend architecture | [spec](specs/EPIC-01-frontend.md) |
| E2 | **Identity & tenancy**: Agency → Clients → users, roles, Postgres row-level security | 🟨 | OAuth/OIDC, RBAC, multi-tenant isolation | [spec](specs/EPIC-02-identity-tenancy.md) |
| E3 | **AI gateway**: Gemini/Claude/OpenAI behind one interface, structured outputs, fallback, per-tenant cost logging | ⬜ | Provider abstraction, resilience, metering | — |
| E4 | **AI quality & observability**: golden-set evals, prompt regression in CI, tracing | ⬜ | LLM evals, Langfuse, OpenTelemetry | — |
| E5 | **Infrastructure & delivery**: Terraform on GCP (Cloud Run, Cloud SQL, GCS), staging and prod | ⬜ | Infrastructure-as-code, CI/CD, secrets | — |
| E6 | **Talent search**: pgvector, hybrid search, reranking, duplicate-person review queue | ⬜ | Embeddings, retrieval quality | — |
| E7 | **Explainable, bias-aware scoring**: cited evidence, blind mode, audit log | ⬜ | Grounding, PII redaction, compliance | — |
| E8 | **Screening agent** with human-in-the-loop approval | ⬜ | LangGraph | — |
| E9 | **"Ask your pipeline"**: RAG chat over candidates | ⬜ | RAG over structured data | — |
| E10 | **MCP server**: use the platform from Claude or ChatGPT | ⬜ | MCP | — |
| E11 | **Commercial**: Stripe billing, ATS connectors, shareable client shortlists | ⬜ | Webhooks, OAuth integrations | — |

Ordering notes:
- E1 follows E0 so that the generated API client targets the settled Person/Application API.
- E1 precedes E2 so that login and the agency/client switcher are built on the new design system.
- E5 follows E2 so that nothing is deployed publicly until authentication and tenant isolation exist.
- E5 must serve the frontend and the API under one registrable domain, preferably one origin with `/api` routed to the backend, so E2's `SameSite=Lax` session cookie works (E2 spec §4.1). Flower's and MinIO's default credentials must not ship.

## Sprints

| Sprint | Dates | Goal | Status |
|---|---|---|---|
| [1](sprints/sprint-01.md) | 2026-10-13 (closed early) | Safety net & working CI: characterization tests on real Postgres, CI running on `master` | done |
| [2](sprints/sprint-02.md) | 2026-10-14 → 10-15 | Modules in place: modular layout with enforced boundaries; parse-jd and skill filter fixed | done |
| [3](sprints/sprint-03.md) | 2026-10-15 → 10-19 | People and applications side by side: expand, dual write, backfill, verify | done |
| [4](sprints/sprint-04.md) | 2026-10-19 (closed early) | Switch reads, contract, close E0 | done |
| [5](sprints/sprint-05.md) | 2026-11-17 (closed 11-18) | E1 foundation: design system & app shell, `/api/v2`, generated client, campaigns and the candidate table | done |
| [6](sprints/sprint-06.md) | 2026-11-18 (closed 11-19) | E1 hero flow end to end: uploads, review panel and Person profile, Home/Insights/Talent, Playwright E2E, old UI removed; close E1 | done |
| [7](sprints/sprint-07.md) | 2026-11-19 → 11-26 | E2 part 1: tenancy in the data, row-level security, sign-in core; backend dependency advisories | in progress |
| [8](sprints/sprint-08.md) | after Sprint 7 | E2 part 2: roles and clients, invitations, Google/Microsoft sign-in, auth screens, `/api/v1` removed; close E2 | planned |

Open follow-ups (bugs, upgrades, small debts) that no sprint has taken yet: [backlog.md](backlog.md).

## Out of scope for now

The workflow breadth described in the [PRD](../HireFlow_AI_PRD.md) (candidate portal, video screens, scheduling, offers, e-sign) is out of scope. We revisit it only if a real client asks, and we prefer integrating existing tools (e.g. Cal.com) over building these ourselves.
