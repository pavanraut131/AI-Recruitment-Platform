# Contributing

How work flows through this repo. The full process (planning levels, ticket format, sprint rhythm,
ADRs) lives in [docs/process.md](docs/process.md); the epic order is in [docs/roadmap.md](docs/roadmap.md).

## Workflow

1. **Pick a ticket** from the current sprint file in `docs/sprints/`. Ticket IDs look like `E0-03`.
2. **Branch** from `master`, one branch per ticket, named `e<epic>-<nn>-short-name`, e.g. `e0-03-person-application-split`.
3. **Commit** using [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`.
4. **Open one PR per ticket.** Title it with the ticket ID, e.g. `E0-03: Split Candidate into Person + Application`.
   Fill in the PR template, including the Definition of done checklist.
5. **Merge** once CI is green and the checklist is complete.

### Stacked PRs

When a ticket depends on another ticket whose PR hasn't merged yet:

1. Branch from the parent ticket's branch instead of `master`.
2. Open the PR with the parent's branch as its **base**, so the diff shows only your ticket's changes.
3. After the parent merges, retarget your PR to `master` (Edit → base branch) and rebase or merge `master` in if needed.

## Local checks before pushing

```bash
make lint        # ruff check + ruff format --check
make typecheck   # mypy
make test        # pytest
pre-commit install   # once per clone; runs the hooks on every commit
```

> These Make targets and the pre-commit config arrive with ticket **E0-02**. Until it merges they may not exist yet.

## Branch protection on `master` (repo owner, one-time)

Do this after ticket **E0-05** (the CI rewrite) has merged and its workflow has run at least once.
GitHub only lists a status check in the picker after it has reported on the repo.

1. On GitHub, open the repo and go to **Settings → Branches** (or **Settings → Rules → Rulesets** for the newer rulesets UI).
2. Click **Add branch protection rule** (or **New ruleset → New branch ruleset**) and target the branch `master`.
3. Enable **Require a pull request before merging**. Required approvals can be **0** for a solo repo.
4. Enable **Require status checks to pass before merging** and add these checks:
   - `backend-quality`
   - `backend-test`
   - `migrations`
   - `frontend`
   - `compose`
5. Enable **Require branches to be up to date before merging**.
6. Make sure **force pushes** and **deletions** are blocked (leave "Allow force pushes" and "Allow deletions" unchecked, or enable "Block force pushes" and "Restrict deletions" in a ruleset).
7. Save the rule. Check it works by pushing a commit directly to `master`; it should be rejected.
