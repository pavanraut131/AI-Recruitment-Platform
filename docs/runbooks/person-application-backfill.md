# Runbook · Person/Application backfill (EPIC-00 phase 2 and 3)

Converts every legacy `candidates` row into People (`talent`) and Applications (`hiring`), then proves the result before the Sprint 4 read switch. Spec: [EPIC-00 §6.1–6.3](../specs/EPIC-00-platform-core.md#6-migration-plan-expand--backfill--contract). Code: `app/modules/ingestion/backfill.py`, `app/modules/ingestion/verify_backfill.py` (their module docstrings hold the details).

The backfill is **online** (the app keeps running and uploading), **additive** (it never changes or deletes a legacy row), **idempotent** and **resumable**.

## 1. Preconditions

| Check | How |
|---|---|
| Migrations at head (`b7d2e4f61a90`, E0-16; E0-17 adds none) | `docker compose exec backend alembic current` shows `(head)` |
| The dual write **with legacy linking** (E0-16 + E0-17) is deployed to **every worker**, and the workers were restarted after the deploy | `docker compose restart worker_extract worker_ai`; then upload one resume and check `SELECT id FROM applications WHERE id = '<its candidate id>'` returns a row |
| `PHONE_DEFAULT_REGION` is the same for the workers and the process running the backfill | `.env` |
| A database backup exists (production) | your backup tooling |
| No other backfill run is in progress | `SELECT * FROM backfill_checkpoints` + process list. Two concurrent runs are safe (same locks, idempotent writes) but pointless |

Why the worker restart matters: from E0-17 the pipeline links each new candidate to its application itself (the application reuses the candidate id, or a `legacy_candidate_map` row redirects it). A worker still on E0-16 code creates applications with random ids and no map rows; the backfill repairs those it reaches, but not ones processed after it passed them.

## 2. Order of operations

1. Deploy, migrate, restart workers (section 1).
2. Run the backfill (section 3). Uploads may continue.
3. Run the verification (section 5). It must exit `0`.
4. Go/no-go for the Sprint 4 read switch (section 8).

## 3. Run

```bash
docker compose exec backend python -m app.modules.ingestion.backfill
```

Options:

| Flag | Default | Use |
|---|---|---|
| `--batch-size N` | 500 | Candidates per page (keyset pagination on `candidates (created_at, id)`) and per checkpoint |
| `--max-batches N` | none | Stop after N pages (a controlled partial run) |
| `--checkpoint-name NAME` | `person_application_v1` | Row in `backfill_checkpoints`; a new name starts a new, independent run |
| `--reset-checkpoint` | off | Start again from the first candidate. Already backfilled candidates are skipped, so on a finished database it changes nothing |

How it commits: **one transaction per candidate** (resolve the Person, upsert the Application, map row, recruiter fields, `resumes.person_id` / `extracted`), and the checkpoint after each page. One transaction per candidate keeps `resolve_person`'s advisory locks to at most 3 per transaction for milliseconds, exactly like one more pipeline worker, and keeps spec §5.4's lock-ordering argument (no deadlock with live uploads) valid. A page-sized transaction would hold every lock of the page until its commit (measured: 193 locks for the 86 candidates of the small test seed, so roughly 1,100 to 1,500 for a page of 500) and could deadlock with a live upload.

Expected output (Compose seed, 1,125 candidates, an empty dual-write state):

```
backfill: 'person_application_v1' starting after (start), batch size 500
backfill: batch 1: 500 candidates, processed 500, skipped 0; checkpoint (2025-01-07T...)
backfill: batch 2: 500 candidates, processed 500, skipped 0; checkpoint (...)
backfill: batch 3: 125 candidates, processed 125, skipped 0; checkpoint (...)
backfill: finished after 3 batch(es) in <seconds>s
  started after:          (start)
  checkpoint:             (2025-01-08T..., ...)
  candidates read:        1125
  processed:              1125
  skipped:                0
  people created:         808
  people matched:         317
  resolutions reused:     0 (resolved by the dual write)
  identity conflicts:     4
  applications created:   1101
  applications merged:    24 (candidate joined an existing one)
  legacy map rows:        24
  extractions stored:     1125
  recruiter fields moved: ... application(s)
```

Live uploads during the run add candidates at the end of the keyset order: depending on timing they show up as `skipped (already_backfilled N)` (the pipeline linked them) or `skipped (resume_not_done N)` (still processing; the pipeline links them when it finishes). Exit code: `0` on success, `1` if a candidate failed unexpectedly (the run stops; see section 4).

**Skip reasons** (logged as `backfill_candidate_skipped` with `candidate_id` and `reason`, never fatal):

- `resume_not_done`: the resume is `pending`, `extracting`, `processing`, `failed` or `quota_exceeded`. Nothing to backfill; when it reaches `done` through the pipeline, the pipeline links it. A legacy resume that later becomes `done` some other way is picked up by `--reset-checkpoint`.
- `resume_missing`: the candidate's resume row does not exist (cannot happen while `candidates.resume_id` has its foreign key; kept for after phase 5).
- `already_backfilled`: the resume has `person_id` and `extracted`, and the candidate already leads to an application.

## 4. Monitor and resume

- **Progress:** one line per page on stdout; `SELECT * FROM backfill_checkpoints;` shows the last committed position.
- **Errors:** `backfill_candidate_failed` in the log (with the traceback) and exit code `1`.
- **Locks:** `SELECT count(*) FROM pg_locks WHERE locktype = 'advisory';` stays small (at most 3 per running resolution). Upload latency in Flower should not change.
- **Stop at any time** (Ctrl-C, container restart, kill). At most the candidate in flight is lost (rolled back).
- **Resume:** run the same command again. It starts after the checkpoint, so it re-reads at most one page, and the candidates of that page that were already committed are skipped as `already_backfilled`. The final state is the same as an uninterrupted run (tested: `tests/ingestion/test_backfill.py`).
- **After a failure:** read the traceback, fix the cause (data or code), run again.

## 5. Verify

```bash
docker compose exec backend python -m app.modules.ingestion.verify_backfill
# on a seeded stack, compare against the generator's expectations as well:
docker compose exec backend python -m app.modules.ingestion.verify_backfill --manifest /app/seed-manifest.json
```

Read-only, one consistent snapshot (`REPEATABLE READ`). Exit `0` = every check passed, `1` = a check failed, `2` = bad arguments or unreadable manifest. Each failing check lists up to 10 offending ids.

| Check | Meaning of a failure | Typical fix |
|---|---|---|
| `candidates_lead_to_one_application` | A candidate with a `done` resume is neither an application id nor in `legacy_candidate_map` (or is both, or leads to another campaign or person) | Run the backfill with `--reset-checkpoint`; if it persists, a worker is on pre-E0-17 code |
| `applications_have_people` | An application points at a missing Person | Investigate; nothing deletes People today |
| `applications_have_resumes` | An application's resume is missing, in another campaign or resolved to another person | A candidate/resume was deleted through the legacy API after its application existed (the legacy delete does not delete applications yet). Note for the Sprint 4 switch |
| `done_resumes_have_people` | A `done` resume has no `person_id`, or it disagrees with `person_resumes` | Backfill with `--reset-checkpoint` |
| `identifiers_unique` | A `(kind, value_normalized)` stored twice | Cannot happen with the unique constraint; stop and investigate |
| `people_without_identifiers_justified` | A Person without identifiers has a resume with a usable one (valid email, or a match-eligible phone, ADR 0004 option A) | Investigate the resolution of that resume |
| `people_have_resumes` | A Person no resume resolved to | Investigate |
| `expected.*` / `counts.legacy_candidates` (with `--manifest`) | Numbers differ from the seed manifest | Wrong manifest for this database, uploads into seeded campaigns, or a matching bug |

With `--manifest`, the counts are taken **only in the manifest's `campaign_ids`** (seed manifests written from E0-17 on list them), so uploads to other campaigns during the rehearsal do not change them. The integrity checks always cover the whole database.

## 6. Roll back

The backfill is additive: legacy tables are untouched and nothing reads the new tables before the Sprint 4 switch. In order of preference:

1. **Leave the data.** Nothing reads it; fix the cause, then `--reset-checkpoint` and run again.
2. **Undo the backfill, keep the dual write.** Only rows the backfill wrote, but they are mixed with the dual write's, so this is all or nothing: stop the workers first (`docker compose stop worker_extract worker_ai`), then

   ```sql
   BEGIN;
   TRUNCATE people, person_identifiers, identity_conflicts, person_resumes,
            applications, legacy_candidate_map, backfill_checkpoints;
   UPDATE resumes SET person_id = NULL, extracted = NULL WHERE person_id IS NOT NULL OR extracted IS NOT NULL;
   COMMIT;
   ```

   then start the workers and run the backfill again from scratch (it rebuilds what the dual write had written as well). Clearing `extracted` makes a retried pipeline task call the AI extraction again for resumes not yet `done`; leave `extracted` alone if quota matters.
3. **Remove the new model entirely** (only together with reverting the dual-write code): `alembic downgrade 5618884e3aec` drops the E0-14/15/16 expand tables and columns. Legacy data is unaffected.

## 7. Rehearsal on the Compose stack

Seed 1,125 legacy candidates, upload resumes while the backfill runs, verify. Run from the repository root with this branch checked out (the backend and workers mount `./backend`).

```bash
# 0. Code and schema: restart the workers so they run the E0-17 pipeline.
docker compose restart worker_extract worker_ai
docker compose exec backend alembic upgrade head

# 1. Fresh seed (also empties the People/Application tables and the checkpoints).
docker compose exec backend python -m app.devtools.seed --people 800 --campaigns 15 --seed 42 --reset
#    -> "expected after backfill: 808 people, 1101 applications, 4 identity conflicts"

# 2. In a second terminal, start live uploads into a new campaign (40 resumes; one of them
#    reuses a seeded person's email, so it must attach to that seeded Person):
docker compose exec -T backend python - <<'EOF'
import json, httpx, fitz
api = "http://localhost:8000/api/v1"
seeded = json.load(open("/app/seed-manifest.json"))["cases"]["plain"]["examples"][0]["email"]
campaign = httpx.post(f"{api}/campaigns", json={"title": "Rehearsal live uploads", "role": "Backend Engineer", "required_skills": ["Python", "Go"]}).json()
print("live campaign", campaign["id"])
for i in range(40):
    email = seeded if i == 0 else f"live.upload{i}@example.com"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), f"Live Upload\n{email}\nBackend Engineer, {i % 9 + 1} years\nSkills: Python, Go, Docker\nB.Tech in Computer Science", fontsize=11)
    r = httpx.post(f"{api}/campaigns/{campaign['id']}/upload", files=[("files", (f"live{i}.pdf", doc.tobytes(), "application/pdf"))])
    print(i, r.status_code)
EOF

# 3. At the same time, in the first terminal:
docker compose exec backend python -m app.modules.ingestion.backfill

# 4. When the uploads are done (all 40 resumes `done` in the UI or Flower), verify:
docker compose exec backend python -m app.modules.ingestion.verify_backfill --manifest /app/seed-manifest.json
```

The upload script assumes `AI_PROVIDER=fake` (deterministic, no quota); with Gemini the uploads also work but may hit the free-tier quota (`quota_exceeded` resumes are skipped by the backfill and do not affect the scoped numbers).

Expected: exit `0`; `expected.people 808`, `expected.applications 1101`, `expected.identity_conflicts 4`, `expected.legacy_candidate_map_rows 24`, `counts.legacy_candidates 1125`, each `actual` equal; total applications 1,141 (the 40 live ones) and 847 people (39 new live people; the 40th matched a seeded Person). The rehearsal is also automated at both sizes in `tests/ingestion/test_backfill.py`.

Optional: run the backfill a second time (`--reset-checkpoint`) and confirm `processed: 0`, every candidate `already_backfilled`.

## 8. Go/no-go for the Sprint 4 read switch

Go only if **all** hold:

- [ ] `verify_backfill` exits `0` on the target environment, run after uploads have drained.
- [ ] The backfill summary has no unexplained skips (`resume_not_done` only for resumes genuinely in progress or failed).
- [ ] The Compose rehearsal (section 7) passed with uploads running, with the numbers above.
- [ ] A second backfill run reports `processed: 0`.
- [ ] Workers have run the E0-17 pipeline since before the backfill started (section 1).
- [ ] **Recruiter edits after the backfill are accounted for.** Until the switch, the legacy endpoints (`PATCH /candidates/{id}/...`) still write only `candidates`: a stage or note changed after a candidate was backfilled is not on its application, and re-running the backfill does not copy it (the candidate is `already_backfilled`). The switch must either re-sync `pipeline_stage` / `notes` from `candidates` (newest non-null per field, `hiring.service.carry_recruiter_fields`) immediately before reads move, or the legacy writes must be mirrored first.
- [ ] Candidates deleted through the legacy API after the backfill: their applications remain (`applications_have_resumes` fails for them). Decide in the switch ticket whether the delete endpoints also delete the application.

No-go: fix, re-run the backfill (`--reset-checkpoint` if needed) and verify again.

## Reference: rules the backfill applies

- **Profile and scoring follow upload time, not processing order.** `resolve_person` refreshes the profile from the most recently *uploaded* resume; `upsert_application` keeps the scoring of the newest resume (`uploaded_at`, ties by resume id). Identity matching itself is order-sensitive in one case (a resume carrying one person's email and another's phone creates a conflict only if both People exist), which is why candidates are processed in `created_at` order, the order they were uploaded in.
- **Application ids:** the first candidate of a `(campaign, person)` pair processed keeps its id; every other candidate of the pair gets a `legacy_candidate_map` row.
- **Recruiter fields** (`pipeline_stage`, `notes`): per field, the value of the **newest candidate (by resume upload time) that has one** among all candidates of the application. A candidate without a value never clears one. Independent of processing order.
- **Category** travels with the scoring (newest resume wins), including a manual change on that candidate; a manual category on an older, merged candidate is not kept.
