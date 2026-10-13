# Roadmap

**Product:** an AI hiring-intelligence layer for **recruitment agencies**. It turns every resume an agency has ever received into a searchable, explainable, reusable talent pool.

**Approach:** a modular monolith (see [ADR 0001](adr/0001-modular-monolith.md)). We build each layer properly before pitching to clients. Working process is described in [process.md](process.md).

**Tenancy model:** Agency → Clients → Campaigns. People live in the agency-wide talent pool, and each one can have Applications to many campaigns.

## Epics

Status values: ⬜ not started · 🟦 designing · 🟨 in progress · ✅ done

| # | Epic | Status | Core concepts | Spec |
|---|---|---|---|---|
| E0 | **Platform core**: modular restructure, Person/Application split, Postgres test infra, working CI | 🟦 | DDD, testcontainers, data migrations | [spec](specs/EPIC-00-platform-core.md) |
| E1 | **Product design & frontend foundation**: agency-model screen structure, design system (shadcn/ui, tokens, light/dark), TanStack Query, API client generated from OpenAPI, rebuild existing screens, Playwright | ⬜ | Product/UX design, design systems, frontend architecture | — |
| E2 | **Identity & tenancy**: Agency → Clients → users, roles, Postgres row-level security | ⬜ | OAuth/OIDC, RBAC, multi-tenant isolation | — |
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

## Sprints

| Sprint | Dates | Goal | Status |
|---|---|---|---|
| [1](sprints/sprint-01.md) | 2026-10-14 → 10-20 | Safety net & working CI: characterization tests on real Postgres, CI running on `master` | in review |

## Out of scope for now

The workflow breadth described in the [PRD](../HireFlow_AI_PRD.md) (candidate portal, video screens, scheduling, offers, e-sign) is out of scope. We revisit it only if a real client asks, and we prefer integrating existing tools (e.g. Cal.com) over building these ourselves.
