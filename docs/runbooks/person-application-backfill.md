# Runbook · Person/Application backfill and read switch (EPIC-00 phases 2 to 4)

Converts every legacy `candidates` row into People (`talent`) and Applications (`hiring`), proves the result, then switches the API's reads to them (section 8, E0-19). Spec: [EPIC-00 §6.1–6.3](../specs/EPIC-00-platform-core.md#6-migration-plan-expand--backfill--contract). Code: `app/modules/ingestion/backfill.py`, `app/modules/ingestion/verify_backfill.py` (their module docstrings hold the details).

The backfill is **online** (the app keeps running and uploading), **additive** (it never changes or deletes a legacy row), **idempotent** and **resumable**.

## 1. Preconditions

| Check | How |
|---|---|
| Migrations at head (`b7d2e4f61a90`, E0-16; E0-17 and E0-18 add none; E0-19's two are applied by the switch, section 8.2) | `docker compose exec backend alembic current` shows `(head)` |
| The dual write **with legacy linking** (E0-16 + E0-17) is deployed to **every worker**, and the workers were restarted after the deploy | `docker compose restart worker_extract worker_ai`; then upload one resume and check `SELECT id FROM applications WHERE id = '<its candidate id>'` returns a row |
| `PHONE_DEFAULT_REGION` is the same for the workers and the process running the backfill | `.env` |
| A database backup exists (production) | your backup tooling |
| No other backfill run is in progress | `SELECT * FROM backfill_checkpoints` + process list. Two concurrent runs are safe (same locks, idempotent writes) but pointless |

Why the worker restart matters: from E0-17 the pipeline links each new candidate to its application itself (the application reuses the candidate id, or a `legacy_candidate_map` row redirects it). A worker still on E0-16 code creates applications with random ids and no map rows; the backfill repairs those it reaches, but not ones processed after it passed them.

## 2. Order of operations

1. Deploy, migrate, restart workers (section 1).
2. Run the backfill (section 3). Uploads may continue.
3. Run the verification (section 5). It must exit `0`.
4. Go/no-go for the Sprint 4 read switch, then the switch itself (section 8, E0-19).

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
| `--resync-recruiter-fields` | off | Instead of backfilling: recompute every application's `pipeline_stage`, `notes` and `category` from its candidates (E0-18, section 5a). `--batch-size` is then applications per page/transaction |

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
| `applications_have_resumes` | An application's resume is missing, in another campaign or resolved to another person | Before E0-18 the legacy delete left applications behind. From E0-18 the delete applies the delete rule (below), so a failure means a delete ran on pre-E0-18 code: investigate, delete the orphaned application by hand |
| `recruiter_fields_consistent` (E0-18 code only) | An application's `pipeline_stage`, `notes` or `category` differs from the rule over its candidates | Recruiter edits made before E0-18 was deployed: run `--resync-recruiter-fields` (section 5a). Removed from the E0-19 code: after the switch the recruiter edits applications |
| `applications_have_extractions` (E0-19) | An application's current resume has no `extracted` snapshot, so its name, contacts and profile would read as empty | Investigate; backfilled and pipeline-processed resumes always have one |
| `done_resumes_have_people` | A `done` resume has no `person_id`, or it disagrees with `person_resumes` | Backfill with `--reset-checkpoint` |
| `identifiers_unique` | A `(kind, value_normalized)` stored twice | Cannot happen with the unique constraint; stop and investigate |
| `people_without_identifiers_justified` | A Person without identifiers has a resume with a usable one (valid email, or a match-eligible phone, ADR 0004 option A) | Investigate the resolution of that resume |
| `people_have_resumes` | A Person no resume resolved to | Investigate |
| `expected.*` / `counts.legacy_candidates` (with `--manifest`) | Numbers differ from the seed manifest | Wrong manifest for this database, uploads into seeded campaigns, or a matching bug |

With `--manifest`, the counts are taken **only in the manifest's `campaign_ids`** (seed manifests written from E0-17 on list them), so uploads to other campaigns during the rehearsal do not change them. The integrity checks always cover the whole database.

## 5a. Re-sync the recruiter fields (E0-18)

*Pre-switch only. The E0-19 code refuses it (exit `2`): after the switch the application is the source of truth and a re-sync from the frozen candidates would undo every recruiter edit since.*

```bash
docker compose exec backend python -m app.modules.ingestion.backfill --resync-recruiter-fields
```

Recomputes every application's `pipeline_stage`, `notes` and `category` from its candidates with the rule below, one transaction per page of `--batch-size` applications (each page's applications are locked `FOR UPDATE` while they are recomputed, so a concurrent API edit waits a few milliseconds and then recomputes on top). Online and idempotent: a second run reports `applications changed: 0`. Expected output:

```
resync: batch 1: 500 applications, changed <n>
...
resync: finished after 3 batch(es) in <seconds>s
  applications checked:   1101
  applications changed:   <n> (category <a>, notes <b>, pipeline_stage <c>)
```

Each change is logged as `resync_recruiter_fields_changed` with the application id and fields. Exit code `0`, or `1` if a page failed (committed pages are kept; run again).

## 6. Roll back

*Before the read switch only. After it the API reads the new tables: roll back the switch instead (section 8.3).*

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

## 8. The read switch (E0-19): go/no-go, procedure, rollback

From E0-19 the legacy candidate endpoints read Applications and the resumes' extraction snapshots, and nothing writes `candidates` (spec §6.1 phase 4, §7). The switch is a deploy of the E0-19 code with two migrations: `e19a0c5d2b71` (expand: `resumes.raw_text`) and `e19b7f3a9c42` (data: each application takes its legacy candidates' timestamps, so the switched responses carry the same `created_at` / `updated_at`).

### 8.1 Go/no-go

Go only if **all** hold. Items 1 to 5 are properties of the target environment, (re)confirmed by the pre-switch steps of 8.2 (automated equivalents: `tests/ingestion/test_backfill.py`, `tests/ingestion/test_read_switch.py`). Items 6 and 7 were code changes, closed by E0-18.

- [ ] `verify_backfill` (E0-18 code) exits `0` on the target environment, run after uploads have drained. *(8.2 step 4. It includes `recruiter_fields_consistent`.)*
- [ ] The backfill summary has no unexplained skips (`resume_not_done` only for resumes genuinely in progress or failed). *(Step 2.)*
- [ ] The Compose rehearsal (section 7) passed with uploads running, with the numbers above. *(Step 2.)*
- [ ] A second backfill run reports `processed: 0`. *(Step 2.)*
- [ ] Workers have run the E0-17 pipeline since before the backfill started (section 1). *(Step 1: E0-18 workers include it.)*
- [x] **Recruiter edits after the backfill are accounted for** (E0-18): the legacy recruiter endpoints mirror every write onto the application, and `--resync-recruiter-fields` (section 5a) repairs edits made before E0-18. Evidence: the E0-18 PR.
- [x] **Candidates deleted through the legacy API after the backfill** (E0-18): the delete rule keeps `applications_have_resumes` green. Evidence: the E0-18 PR.

### 8.2 Switch procedure

Steps 1 to 4 run on the **E0-18** code (they are its pre-switch procedure); step 5 on, the switch itself. Commands are for the Compose stack, from the repository root; the backend and workers mount `./backend`, so checking out a branch deploys its code (the backend reloads, the workers need a restart). The capture files are scratch: delete them afterwards.

1. **Deploy E0-18** (backend and both workers): `git checkout e0-18-switch-gate && docker compose restart backend worker_extract worker_ai`. From here on every legacy write is mirrored.
2. If not already done on this database: run the backfill (section 3) and confirm a second run reports `processed: 0`; for the rehearsal, seed first and expect the section 7 numbers.
3. **Re-sync** the recruiter fields once, to repair edits made before step 1: `docker compose exec backend python -m app.modules.ingestion.backfill --resync-recruiter-fields`. Running it again must report `applications changed: 0`.
4. **Verify**, after uploads have drained: `docker compose exec backend python -m app.modules.ingestion.verify_backfill` (plus `--manifest /app/seed-manifest.json` on a seeded stack; recruiter edits or deletes made through the API since seeding change the expected counts, the integrity checks must still pass). Must exit `0`. Tick 8.1 items 1 to 5 with the outputs in the E0-19 PR.
5. **Capture before** (still on E0-18; uploads drained, no edits from here to step 8). The capture script is standalone (it imports nothing from `app`), so take it from the E0-19 branch without switching code:

   ```bash
   git show e0-19-read-switch:backend/app/devtools/capture_api.py > backend/e019_capture_api.py
   docker compose exec backend python e019_capture_api.py --base-url http://localhost:8000 --out /app/e019-before.json
   rm backend/e019_capture_api.py
   ```

   It requests every legacy `GET` endpoint: campaigns, every campaign's detail, stats and resumes, its candidate list with nine filter combinations (all pages), its CSV export with three, every candidate's detail, every resume's candidate, and `/stats`. About 2,500 requests on the Compose seed, 30 s.
6. **Deploy E0-19**: stop what serves and writes, switch the code, migrate, start.

   ```bash
   docker compose stop backend worker_extract worker_ai
   git checkout e0-19-read-switch
   docker compose run --rm backend alembic upgrade head      # e19a0c5d2b71, e19b7f3a9c42
   docker compose start backend worker_extract worker_ai
   ```

   Both migrations are safe for the E0-18 code too (an extra nullable column; timestamps it does not read), so in an environment that cannot stop, migrate first and deploy right after (an E0-18 write between the two only changes that row's `updated_at`).
7. **Verify** with the E0-19 code: `docker compose exec backend python -m app.modules.ingestion.verify_backfill`. Must exit `0` (spec §6.3 has what it checks after the switch: `recruiter_fields_consistent` is gone, `applications_have_extractions` is new, `without a legacy candidate (new)` must be `0` before any post-switch upload).
8. **Capture after** (`--ids-from` also asks for every id of the first capture, so the merged legacy ids that no list shows any more are checked too) and **compare**:

   ```bash
   docker compose exec backend python -m app.devtools.capture_api --base-url http://localhost:8000 --out /app/e019-after.json --ids-from /app/e019-before.json
   docker compose exec backend python -m app.devtools.compare_api /app/e019-before.json /app/e019-after.json
   rm backend/e019-before.json backend/e019-after.json
   ```

   Must exit `0`. Expected output on the Compose seed (with a few recruiter edits and deletes made in step 4's window, as in the local rehearsal):

   ```
   compare_api: 2467 responses compared
     identical:                         2267
     expected (merged legacy id):       200 (24 merged legacy ids)
     expected (unprocessed resume):     0
     differences:                       0
   compare_api: PASSED
   ```

   *Expected (merged legacy id)*: the seed's 24 merged candidates now answer with their application (its id, its newest resume's data) and appear once in lists, exports and stats; every response containing one is set aside, the other rows of those responses are still compared exactly, and `/stats`' `total_candidates` must drop by exactly the number of merged-away ids. *Expected (unprocessed resume)*: placeholder rows of resumes not `done` (none once uploads have drained; spec §7). Any other line under `differences` is a no-go: roll back (8.3).
9. Smoke test the UI: open a campaign, filter, open a candidate, change its stage, export.

### 8.3 Rollback

Up to E0-20 the `candidates` table is untouched by the switch, so rolling back is redeploying the E0-18 code (step 6 with `e0-18-switch-gate`; the migrations can stay, the E0-18 code ignores both). **The gap:** E0-18 reads `candidates`, and after the switch nothing wrote it:

- resumes processed after the switch have an application but no candidate, so they are missing from the E0-18 lists;
- recruiter edits made after the switch (stage, notes, category) are on applications only;
- deletes after the switch are fine: the resumes went, and their candidates with them (foreign key).

Close it before starting the E0-18 code, in one transaction (stop the workers first):

```sql
BEGIN;
-- 1. A legacy candidate for every scored resume that has none (processed after the switch):
--    the application's current resume, with the application's scoring and recruiter fields,
--    under the application's id when no candidate holds it, else a new id plus a map row.
CREATE TEMP TABLE rollback_new_candidates ON COMMIT DROP AS
SELECT CASE WHEN EXISTS (SELECT 1 FROM candidates c WHERE c.id = a.id) THEN gen_random_uuid()
            ELSE a.id END AS id,
       a.id AS application_id, r.id AS resume_id
FROM applications a JOIN resumes r ON r.id = a.resume_id
WHERE r.status = 'done' AND NOT EXISTS (SELECT 1 FROM candidates c WHERE c.resume_id = r.id);

INSERT INTO candidates (id, resume_id, campaign_id, name, email, phone, github_url, linkedin_url,
    skills, education, work_experience, certifications, projects, years_of_experience, summary,
    strengths, weaknesses, missing_skills, recommendation, score, score_breakdown, category,
    notes, pipeline_stage, raw_text, created_at, updated_at)
SELECT n.id, r.id, a.campaign_id,
       r.extracted->>'name', r.extracted->>'email', r.extracted->>'phone',
       r.extracted->>'github_url', r.extracted->>'linkedin_url',
       coalesce(r.extracted->'skills', '[]'), coalesce(r.extracted->'education', '[]'),
       coalesce(r.extracted->'work_experience', '[]'),
       coalesce(r.extracted->'certifications', '[]'), coalesce(r.extracted->'projects', '[]'),
       (r.extracted->>'years_of_experience')::float,
       a.summary, a.strengths, a.weaknesses, a.missing_skills, a.recommendation, a.score,
       a.score_breakdown, a.category, a.notes, a.pipeline_stage, r.raw_text,
       a.created_at, a.updated_at
FROM rollback_new_candidates n
JOIN applications a ON a.id = n.application_id JOIN resumes r ON r.id = n.resume_id;

INSERT INTO legacy_candidate_map (candidate_id, application_id)
SELECT id, application_id FROM rollback_new_candidates WHERE id <> application_id;

-- 2. Recruiter edits made after the switch: onto the candidate of each application's current
--    (newest) resume, so the E0-18 rule gives the application's values back.
UPDATE candidates c
SET pipeline_stage = a.pipeline_stage, notes = a.notes, category = a.category,
    updated_at = a.updated_at
FROM applications a
WHERE c.resume_id = a.resume_id
  AND (c.pipeline_stage, c.notes, c.category)
      IS DISTINCT FROM (a.pipeline_stage, a.notes, a.category);
COMMIT;
```

Then deploy E0-18 and run its `verify_backfill`: it must exit `0`, `recruiter_fields_consistent` included (rehearsed locally: two post-switch uploads, a new person and a returning one, a post-switch edit and a delete; the SQL inserted 2 candidates and 1 map row, and the E0-18 verify passed). Known limits: an older resume of a merged application processed only after the switch (rare: a resume uploaded earlier but analysed later) gets no candidate, and the legacy rows show each candidate separately again, so a post-switch edit appears on the row of the newest resume. Roll back with uploads drained: a resume still being analysed has its text on the resume, where the E0-18 pipeline does not look, so it would fail; re-upload it, or set its `processing_step` to `NULL` and use retry-failed (which then starts again from the PDF).

From E0-20 (contract) `candidates` is dropped and this rollback no longer exists.

## Reference: rules the backfill applies

- **Profile and scoring follow upload time, not processing order.** `resolve_person` refreshes the profile from the most recently *uploaded* resume; `upsert_application` keeps the scoring of the newest resume (`uploaded_at`, ties by resume id). Identity matching itself is order-sensitive in one case (a resume carrying one person's email and another's phone creates a conflict only if both People exist), which is why candidates are processed in `created_at` order, the order they were uploaded in.
- **Application ids:** the first candidate of a `(campaign, person)` pair processed keeps its id; every other candidate of the pair gets a `legacy_candidate_map` row.
- **Recruiter fields** (`pipeline_stage`, `notes`): per field, the value of the **newest candidate (by resume upload time) that has one** (not `NULL`; an empty note is a value) among all candidates of the application; `NULL` if none has one. A candidate without a value never clears one. Independent of processing order.
- **Category** travels with the scoring (newest resume wins): it is the category of the candidate of the application's current resume, including a manual change on that candidate; a manual category on an older, merged candidate is not kept.
- **One rule everywhere** (E0-18): the backfill, the upload pipeline, the mirrored legacy writes, `--resync-recruiter-fields` and `verify_backfill` all use `hiring.service.expected_recruiter_fields`. A legacy write updates the candidate and then *recomputes* the application from all its candidates; it never copies the new value. *After the switch (E0-19) only the backfill still applies it (on a switched database it links nothing); the API writes the application directly.*
- **Delete rule** (E0-18, `DELETE /candidates/{id}` and bulk delete, same transaction as the candidate and resume delete): the application is deleted with its **last** candidate. Otherwise it is kept, only the deleted candidates' `legacy_candidate_map` rows are removed, and its recruiter fields are recomputed from the remaining candidates. If the deleted candidate's id was the application's id, the application **keeps that id** (the remaining candidates' map rows point at it). If the deleted candidate held the current resume, the application takes the newest remaining candidate's resume and scoring.
