# Backlog

Open follow-ups that no sprint has taken yet. **This is the one place they live**: until E0 closed they were spread over the sprint files (bug table in [Sprint 1](sprints/sprint-01.md#bugs-found-by-e0-04-recorded-as-is-in-the-characterization-snapshots-full-list-in-pr-9), "Backlog" and "rough backlog" sections in Sprints 2 and 3) and the "Notes" of PRs, where they went stale as each sprint closed. Sprint files stay the dated record of what a sprint planned and did; sprint planning pulls items from here and links back.

When an item is picked up, move it into the sprint's board and delete it here. When a PR finds a new follow-up, add it here in the same PR.

*Consolidated in E0-21 (2026-10-19).*

## Bugs found by the characterization tests (E0-04)

Each one is recorded as-is in a snapshot (`BUG (recorded)` comments in `backend/tests/characterization/`), so fixing it is a deliberate, explained snapshot change. Severity as triaged in Sprint 1.

| ID | Bug | Severity | Status |
|---|---|---|---|
| B1 | `POST /campaigns/parse-jd` always 500 | High | ✅ fixed in [#14](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/14) |
| B2 | Candidate list `skill` filter always 500 | High | ✅ fixed in [#15](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/15) |
| B3 | SSE stream not filtered by campaign (cross-tenant leak once E2 adds agencies) | High | ✅ fixed in E0-21 (per-campaign Redis channel, spec §5.1) |
| **B4** | `python-multipart==0.0.9` parses large uploads very slowly (~30 s CPU per 10 MB): a possible DoS on the upload endpoint. Upgrade it (FastAPI's supported range), re-run the upload snapshots. | **High** | open. **Must be fixed before anything is exposed publicly (E5).** Natural home: E2 (auth hardening) or a small standalone PR. |
| B5 | Unvalidated campaign fields (length, `status` free text, nulls in `CampaignUpdate`) and negative `skip`/`limit` give 500 instead of 422 | Medium | open. E1 designs the new campaign API; fix there, or in the legacy API if E1 keeps it longer. |
| B6 | Deletes never removed PDFs from object storage | Medium | ✅ fixed in E0-20 ([#31](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/31)); `app.devtools.storage_orphans` cleans up older stacks |
| B7 | Unknown campaign ids give 200 with empty results on several endpoints (list, export, stream, …) | Medium | open |
| B8 | CSV export ignores `skill` / `min_experience`; `search` didn't escape LIKE wildcards | Medium | **partly fixed:** the `search` escaping was fixed with B2 ([#15](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/15), `app.platform.sql.escape_like`). The export filters are still open (`BUG (recorded, B8)` in `app/api/routes.py`). |
| B9 | Campaign `avg_score` is a mean of per-category means (and drops a 0 mean) | Low | open |
| B10 | `/api/v1/health` listed but not mounted; invalid category/stage gives 400 not 422; error message order random; same 404 for an unknown and an unprocessed resume; `batch_id` never stored; the 500 body echoes the URL | Low | open |

**Suggested order:** B4 first (security, small), then B5/B7/B10 together with E1's new API (they are all "wrong status code" contract fixes, cheaper to do once in the new API than twice), B8/B9 when the stats and export are redesigned in E1.

## Platform and tooling

- **Node 20 → 22 or 24.** Node 20 is end of life. Upgrade `frontend/Dockerfile` (`node:20-alpine`) and the `frontend` CI job (`node-version: 20`) together. Best done at the start of E1, before the frontend rebuild.
- **Index on `resumes.campaign_id`.** The legacy foreign key had none either, so nothing regressed, but the campaign delete (`ingestion` deletes the campaign's resumes, E0-20), the campaign stats' resume counts and the resume list all filter on it. Add it in an expand migration when campaigns get large, or as soon as E2 adds `agency_id` (the index then becomes `(agency_id, campaign_id)`).
- **Remove `/api/v1` after E2.** The `/api/v1/candidates*` endpoints are a compatibility layer over Applications (spec §7) kept for the current frontend, and `legacy_candidate_map` exists only so old candidate ids keep resolving. Once E1's frontend runs on the new resource API and E2 has shipped, remove the legacy routes, their characterization snapshots and `legacy_candidate_map` (a contract migration, like E0-20).
- **Fake OCR returns placeholder text for tiny PDFs.** A PDF whose text layer yields ≤ 100 characters is treated as scanned (`ingestion.pdf`) and sent to the AI service's OCR. With `AI_PROVIDER=fake`, `FakeAIService.transcribe_page_images` returns a fixed placeholder ("Scanned resume (N page(s), digest …)"), so the profile has no name, email or skills and the resume fails as "not a resume". Real Gemini OCR handles such PDFs; only local smoke tests with very small PDFs are affected (found in E0-20's live check). Fix: make the fake OCR return a deterministic, resume-like text derived from the digest (a name, an email, a few vocabulary skills), so the fake pipeline completes, and update `test_scanned_pdf_falls_back_to_ocr_through_the_ai_service`. Workaround meanwhile: use a test PDF with more than 100 characters of text.
- **Branch protection on `master`** requires the five CI checks. The `backend-test` job now also runs the coverage gate (E0-21); no new check name, so nothing to change in the rule.
