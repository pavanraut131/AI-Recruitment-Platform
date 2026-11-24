# Runbook · Tenancy backfill, verify and contract (EPIC-02 phases 2 to 4)

Gives every row written before E2-01 the **default agency** (and every campaign the default client "General"), proves the result, then makes the schema guarantee it: `agency_id NOT NULL`, per-agency uniques and composite foreign keys. Spec: [EPIC-02 §7.1–7.3](../specs/EPIC-02-identity-tenancy.md#7-migration-plan-expand--backfill--contract--enforce). Code: `app/modules/ingestion/backfill_tenancy.py`, `app/modules/ingestion/verify_people_applications.py` and the migration `backend/alembic/versions/e2b02c7a4d19_tenancy_contract.py` (their docstrings hold the details).

The backfill is **online** (the app keeps running and uploading), touches only rows whose `agency_id` (or `client_id`) is `NULL`, never changes an `updated_at`, and is **idempotent** and **resumable** without a checkpoint table. The contract is a migration that takes no long exclusive lock and **refuses to run** until the backfill has finished.

The ids are fixed (`app.platform.tenancy`): default agency `00000000-0000-4000-8000-00000000a001` ("Default agency", slug `default`), default client `00000000-0000-4000-8000-00000000c001` ("General").

## 1. Preconditions

| Check | How |
|---|---|
| E2-01 is deployed (migration `e2a01d5c8f36` applied) **and every worker runs E2-01 code or later**, restarted after the deploy. Older code inserts rows without an agency, which the backfill would miss if they arrive after it passed | `docker compose exec backend alembic current` shows `e2a01d5c8f36`; `docker compose restart worker_extract worker_ai` |
| The E2-02 code is deployed to the backend and the workers (it carries the backfill command, and `resolve_person` matching per agency) | `git log -1`, then `docker compose restart worker_extract worker_ai` |
| A database backup exists (production) | your backup tooling |
| No migration or long-running transaction is in progress | `SELECT pid, state, xact_start, query FROM pg_stat_activity WHERE xact_start < now() - interval '1 minute';` |

Uploads may continue throughout. E2-02's `resolve_person` matches identifiers per agency, and until the contract it also matches the default agency's identifiers that have no agency yet, so an upload of a returning candidate during the backfill still finds their Person.

## 2. Order of operations

1. Deploy E2-01 (done when this runbook starts; section 1).
2. Deploy the E2-02 code **without** migrating: check it out, restart the workers (the backend reloads). Do not run `alembic upgrade head` yet: it would refuse anyway (section 6).
3. **Backfill** (section 3). Uploads may continue.
4. **Verify** (section 5). Must exit `0`.
5. **Contract**: `alembic upgrade head` (section 6).
6. Verify again, and upload one resume to see it processed.

## 3. Run the backfill

```bash
docker compose exec backend python -m app.modules.ingestion.backfill_tenancy
```

| Flag | Default | Use |
|---|---|---|
| `--batch-size N` | 1000 | Rows per batch and per transaction |
| `--max-batches N` | none | Stop after N batches in all (a controlled partial run; exit `0`) |
| `--table T` | every tenant table | Only this table (repeatable): `campaigns`, `applications`, `people`, `person_identifiers`, `identity_conflicts`, `person_resumes`, `resumes` |
| `--status` | off | Only count the rows still missing their agency, per table (read-only). Exit `0` when complete, `1` if not |

What it does, table by table in the order above: the next `--batch-size` rows still missing their agency after the previous batch's last key (keyset on the primary key, never `OFFSET`), updated in **one statement and one transaction per batch**: `UPDATE … SET agency_id = '<default>' WHERE id IN (<next batch>) AND agency_id IS NULL`. Campaigns also get the default client. Each module fills its own tables (hiring, talent, ingestion); the command, in `ingestion`, only drives them.

Locks: only row locks, on at most one batch of one table, for milliseconds, plus the `ROW EXCLUSIVE` table lock any `UPDATE` takes (it blocks nothing but DDL). A batch waits at most 2 s (`lock_timeout`) for a row a live transaction holds; then, or on a deadlock (a live transaction locking two rows of the batch in the other order, e.g. a campaign delete cascading to its applications), it is rolled back and retried, up to 3 times.

Output of the rehearsal (pre-E2-01 Compose seed: 15 campaigns, 1,125 resumes, 808 people; `--batch-size 25`, with 40 uploads running at the same time):

```
backfill_tenancy: agency 00000000-0000-4000-8000-00000000a001, client 00000000-0000-4000-8000-00000000c001, batch size 25
backfill_tenancy: campaigns: batch 1: 15 rows (up to …)
backfill_tenancy: applications: batch 1: 25 rows (up to …)
…
backfill_tenancy: finished in 1.89s
  table                rows filled  batches  retries  seconds  still missing
  campaigns                     15        1        0     0.05              0
  applications                1101       45        0     0.40              0
  people                       808       33        0     0.24              0
  person_identifiers          1448       58        0     0.55              0
  identity_conflicts             4        1        0     0.01              0
  person_resumes              1125       45        0     0.37              0
  resumes                     1125       45        0     0.27              0
backfill_tenancy: complete
```

Rows written by E2-01 code already have their agency and are not counted. Exit code: `0` when every table is complete (or `--max-batches` stopped the run), `1` when rows are still missing their agency at the end or a batch failed, `2` for a bad command line.

## 4. Monitor, stop and resume

- **Progress:** one line per batch on stdout. From another terminal, `docker compose exec backend python -m app.modules.ingestion.backfill_tenancy --status`, or in SQL:

  ```sql
  SELECT 'campaigns' AS t, count(*) FROM campaigns WHERE agency_id IS NULL OR client_id IS NULL
  UNION ALL SELECT 'applications', count(*) FROM applications WHERE agency_id IS NULL
  UNION ALL SELECT 'people', count(*) FROM people WHERE agency_id IS NULL
  UNION ALL SELECT 'person_identifiers', count(*) FROM person_identifiers WHERE agency_id IS NULL
  UNION ALL SELECT 'identity_conflicts', count(*) FROM identity_conflicts WHERE agency_id IS NULL
  UNION ALL SELECT 'person_resumes', count(*) FROM person_resumes WHERE agency_id IS NULL
  UNION ALL SELECT 'resumes', count(*) FROM resumes WHERE agency_id IS NULL;
  ```

- **Retries** are logged as `backfill_tenancy_batch_retry` (table, sqlstate `55P03` lock timeout or `40P01` deadlock) and counted in the summary. A few are normal under load; many on one table mean a long transaction holds its rows: find it in `pg_stat_activity`.
- **Stop at any time** (Ctrl-C, container restart). At most the batch in flight is rolled back.
- **Resume:** run the same command again. Filled rows are never selected again, so it starts at the first row still missing its agency. The final state is the same as an uninterrupted run (tested: `tests/ingestion/test_backfill_tenancy.py`).
- **"Rows are still missing their agency" (exit `1`) after a complete pass:**
  - Rows inserted by pre-E2-01 code during the run (a worker not restarted): restart the workers, run again.
  - A campaign of another agency without a client: the backfill never gives it the default agency's client (it would belong to the wrong agency). Only possible if something wrote such a row by hand; give it one of its agency's clients.

## 5. Verify

```bash
docker compose exec backend python -m app.modules.ingestion.verify_people_applications
# on a seeded stack, compare against the generator's expectations as well:
docker compose exec backend python -m app.modules.ingestion.verify_people_applications --manifest /app/seed-manifest.json
```

Read-only, one consistent snapshot (`REPEATABLE READ`), exit `0` = every check passed. The E2 checks (spec §7.3):

| Check | Meaning of a failure | Typical fix |
|---|---|---|
| `tenancy_complete` | A tenant row has no `agency_id`, or an `agency_id` that is not in `agencies` | Run the backfill (section 3). An unknown agency is a bug: investigate the row |
| `cross_module_same_agency` | An application's campaign, Person or resume, or a resume's campaign or Person, is in another agency (no foreign key can cross modules, ADR 0003) | Before E2-03 every row is in the default agency, so this cannot happen; investigate |
| `clients_same_agency` | A campaign has no client, an unknown one, or another agency's | Run the backfill; otherwise see section 4 |
| `identifiers_unique` | An identifier stored twice **in one agency** (the same value in two agencies is two People, by design) | Cannot happen with the unique constraint; investigate |

The E0 checks (`applications_have_people`, …) are unchanged; [the E0 runbook](person-application-backfill.md#5-verify) describes them.

## 6. Contract

```bash
docker compose exec backend alembic upgrade head      # e2b02c7a4d19
```

It first checks that no tenant row is missing its agency (and no campaign its client). If any is, it **refuses**, names the tables and changes nothing:

```
BackfillIncompleteError: The tenancy backfill has not completed, so the contract cannot run: campaigns: 15 row(s) without agency_id; … Run `python -m app.modules.ingestion.backfill_tenancy` until it reports `complete`, …
```

Then, without a long exclusive lock (each `ACCESS EXCLUSIVE` step changes only the catalog and waits at most 5 s for its lock):

1. *Transaction:* `CHECK (agency_id IS NOT NULL) NOT VALID` on each table (and `client_id` on campaigns).
2. *Autocommit:* `VALIDATE` each check (a scan under `SHARE UPDATE EXCLUSIVE`: reads and writes go on). `CREATE [UNIQUE] INDEX CONCURRENTLY` for `UNIQUE(agency_id, id)` on campaigns and people, `UNIQUE(agency_id, kind, value_normalized)` on person_identifiers, and the indexes `campaigns (agency_id, status, updated_at)`, `people (agency_id, updated_at)`, `resumes (agency_id, campaign_id)`.
3. *Transaction:* `SET NOT NULL` (no scan: the validated check proves it) and drop the checks; the unique indexes become constraints (`ADD CONSTRAINT … UNIQUE USING INDEX`); the global `UNIQUE(kind, value_normalized)` goes; the composite foreign keys are added `NOT VALID` (enforced for new rows at once).
4. *Autocommit:* `VALIDATE` each foreign key.
5. *Transaction:* drop the single-column foreign keys the composite ones replace.

**If it fails half-way** (lock timeout, a cancelled index build), run `alembic upgrade head` again: each step checks the catalog first, rebuilds an invalid index and skips what is done. The revision moves only when everything succeeded. The steps already applied are harmless for the E2-01 and E2-02 code.

- **Lock timeout** (`canceling statement due to lock timeout`): something held one of the tables for more than 5 s (a long transaction, `VACUUM FULL`, a dump). Find it in `pg_stat_activity`, wait or end it, run again.
- **A `CONCURRENTLY` build waits** for every transaction that started before it. It does not block anything while it waits, but the migration does not finish until they end: check `pg_stat_activity` for an `idle in transaction` session.

Then verify (section 5) and upload a resume (section 9.1 of the [E0 runbook](person-application-backfill.md#91-campaign-delete-and-upload-by-hand) has the commands). `alembic check` must report no differences.

## 7. Roll back

**Before the contract**, nothing needs undoing: the backfill only filled columns that nothing reads yet (E2-03 starts reading them). To return to E2-01 code, check it out and restart the workers; the filled values stay, which E2-01 code ignores.

**After the contract:**

```bash
docker compose exec backend alembic downgrade e2a01d5c8f36
```

One transaction (lock timeout 5 s): the single-column foreign keys and `UNIQUE(kind, value_normalized)` come back under their old names, the composite keys, per-agency uniques and indexes go, and the columns become nullable again. **The values stay.** It re-validates the restored keys and builds the old unique index under lock, so on a large database plan a short write pause. It refuses while two agencies store the same identifier (impossible before E2-05 creates a second agency). Then deploy the E2-01 code (or keep E2-02's: it runs on the expanded schema too). Upgrading again needs no new backfill.

## 8. Rehearsal on a throwaway Compose stack

Run it next to the dev stack, never on it: its own Compose project (`-p hireflow-rehearsal`), no fixed container names, its own volumes, and only the API published on a free port, configured from `.env.example` with the fake AI (the E2E override does all of that). The code is baked into the images, so each "deploy" is a rebuild from a checkout (or a `git archive` export) of that commit; run `compose` from it. The seed manifest is written inside the backend container, so copy it out before a rebuild replaces the container, and back in after.

```bash
export E2E_BACKEND_PORT=28012
compose() { docker compose -p hireflow-rehearsal --env-file .env.example   -f docker-compose.yml -f docker-compose.e2e.yml "$@"; }

# 0. Data written before E2-01: at the commit before it (80907bf), migrate and seed.
compose up --build -d --wait postgres redis minio backend
compose exec -T backend alembic upgrade head                      # e20c4a7d1b93
compose exec -T backend python -m app.devtools.seed --people 800 --campaigns 15 --seed 42 --manifest /tmp/seed-manifest.json

# 1. Deploy E2-01 (d8581cb) and migrate: every existing row has agency_id NULL. A few uploads
#    with E2-01 code give the mixed state production has (new rows with the agency).
compose up --build -d --wait postgres redis minio backend worker_extract worker_ai
compose exec -T backend alembic upgrade head                      # e2a01d5c8f36

# 2. Deploy E2-02 (this branch), without migrating. The contract refuses:
compose up --build -d --wait postgres redis minio backend worker_extract worker_ai
compose exec -T backend alembic upgrade head                      # BackfillIncompleteError
compose exec -T backend python -m app.modules.ingestion.backfill_tenancy --status

# 3. In a second terminal: live uploads (40 PDFs, the first reusing a seeded person's email),
#    the E0 runbook's script (section 7) with `api = "http://localhost:8000/api/v1"`. Meanwhile:
compose exec -T backend python -m app.modules.ingestion.backfill_tenancy --batch-size 25
compose exec -T backend python -m app.modules.ingestion.backfill_tenancy   # fills 0 rows
compose exec -T backend python -m app.modules.ingestion.verify_people_applications --manifest /tmp/seed-manifest.json

# 4. Contract (with more uploads running), then check.
compose exec -T backend alembic upgrade head                      # e2b02c7a4d19
compose exec -T backend alembic check
compose exec -T backend python -m app.modules.ingestion.verify_people_applications --manifest /tmp/seed-manifest.json

# 5. Remove it, with its images.
compose down --volumes --remove-orphans --rmi local
```

Expected: the contract refuses in step 2, the backfill reports `complete`, a second run fills `0` rows everywhere, both verifications exit `0` (`expected.people 808`, `expected.applications 1101`, `expected.identity_conflicts 4`), and the live upload of the seeded email joins the seeded Person. Measured (Windows host, Docker Desktop, under load from other stacks): backfill 1.9 s at 25 rows per batch, contract 2 to 8 s and downgrade 3 to 13 s, wall clock with the Alembic process start-up included. Uploads made during the backfill and during the contract were all processed.
