# 0001 · Modular monolith over rewrite or microservices
**Status:** accepted  ·  **Date:** 2026-10-13

## Context
The existing app (~4k lines) consists of FastAPI, Celery workers, PostgreSQL, Redis, MinIO, Gemini and a Next.js frontend. It works, but it is organised by technical layer (`api/`, `models/`, `services/`) rather than by business domain. We plan to grow it into a multi-tenant SaaS with identity, an AI gateway, search, agents and billing. It is built by one developer, there are no users yet, and the goals are both a sellable product and deep learning.

## Decision
Restructure **in place** into a **modular monolith**: one deployable backend and one database, with the code split into domain modules (e.g. `identity`, `talent`, `hiring`, `ai`, `billing`). Modules interact only through each module's public service interface and never import another module's models or query its tables directly. Celery remains the mechanism for background work.

## Alternatives considered
- **Greenfield v2 in a monorepo:** gives a clean slate, but rebuilds working features and delays visible progress. The codebase is small enough to restructure for less effort.
- **Microservices on Kubernetes:** valuable for learning inter-service communication, but the operational overhead (networking, distributed transactions, per-service deployments) would dominate a solo project with no scale requirements. Distributed tracing and messaging can be learned later by extracting one module.

## Consequences
- **Easier:** single deploy, a single transaction boundary, simple local development and fast refactors.
- **Harder:** module boundaries are enforced only by convention, so an import-linting rule is needed to stop modules reaching into each other.
- **Watch for:** any module that is a natural candidate for extraction (e.g. `ai` workers or search) if load demands it. Clean interfaces keep that option open.
