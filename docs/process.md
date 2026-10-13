# How We Work

A lightweight, solo-friendly agile process. Plan near-term work in detail, far-term work roughly.

## Planning levels

| Level | Where | Detail |
|---|---|---|
| Roadmap | [roadmap.md](roadmap.md) | One line per epic. Order can change. |
| Epic | `specs/EPIC-XX-<name>.md` | Full design spec, written before any of its tickets start. |
| Sprint | `sprints/sprint-XX.md` | 1 week, 4–8 tickets, all ready to start. |
| Next sprint | bottom of the current sprint file | Rough ticket list, refined at sprint end. |

Never plan more than ~2 weeks of tickets ahead.

## Epic lifecycle

1. **Design**: discuss approaches, agree on the design.
2. **Spec**: write `specs/EPIC-XX-<name>.md` and review it.
3. **Plan**: break the spec into tickets and add them to sprints.
4. **Build**: work the tickets, one branch and one PR each.
5. **Close**: demo, update the roadmap, and write ADRs for any decisions made along the way.

## Sprint rhythm (1 week)

- **Start**: choose tickets and write a one-sentence **sprint goal**.
- **During**: move each ticket's status `todo → doing → review → done`.
- **End**: run a short demo of what works, write a three-line retro (keep / drop / try), and refine next sprint's tickets.
- Every sprint must end with something that **runs**, not half-built layers.

## Ticket format

Ticket IDs are `E<epic>-<number>`, e.g. `E0-03`.

```markdown
### E0-03 · Short imperative title
**Status:** todo | doing | review | done  ·  **Size:** S | M | L  ·  **Branch:** `e0-03-short-name`

**Goal:** One sentence: what and why.

**Acceptance criteria**
- [ ] Checkable outcome
- [ ] Checkable outcome

**Concept to learn:** e.g. Postgres row-level security policies.

**Notes:** Optional links, gotchas, follow-ups.
```

Sizes: **S** is ≤ half a day, **M** is 1–2 days and **L** is 3–4 days. Split anything bigger than L.

## Definition of done

A ticket is done when all of the following hold:

- [ ] Acceptance criteria met
- [ ] Tests written and passing locally and in CI
- [ ] Lint and type checks clean
- [ ] Docs, README or ADR updated if behaviour or architecture changed
- [ ] Merged through a PR (even solo) with a description of what changed and why

## Architecture Decision Records

Record every significant technical choice in `adr/NNNN-title.md` using this format:

```markdown
# NNNN · Title
**Status:** proposed | accepted | superseded by NNNN  ·  **Date:** YYYY-MM-DD

## Context
What forces are at play?

## Decision
What we chose.

## Alternatives considered
What else we looked at, and why we rejected it.

## Consequences
What gets easier, what gets harder, and what we must now watch for.
```

## Git conventions

- One branch per ticket, named `e<epic>-<nn>-short-name`.
- Use Conventional Commits: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`.
- Reference the ticket ID in the PR title, e.g. `E0-03: Split Candidate into Person + Application`.
