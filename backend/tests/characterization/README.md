# Characterization tests: the API contract

The snapshot tests in this folder record **exactly what every HTTP endpoint returns today**: status
codes, response bodies, relevant headers, the CSV export, the SSE event payloads, the OpenAPI
schema and, for uploads, the Celery tasks dispatched and the objects written to storage.

They are a golden master. They exist so that the platform restructure (EPIC-00) and the
Person/Application switch can be proven not to change the API. They describe **current**
behaviour, not intended behaviour: some snapshots deliberately record bugs (look for
`BUG (recorded)` comments). Fixing such a bug is an intentional contract change.

## Rules

- **These snapshots define the API contract. Do not update them casually.**
- A failing snapshot means the API changed. First decide whether that change is intended.
- Update snapshots only on purpose, with `--snapshot-update`, and only for the tests you meant to
  change:

  ```sh
  uv run pytest tests/characterization/test_candidates.py -k export --snapshot-update
  ```

- **Every snapshot change in a PR must be explained in that PR** (the "Snapshot / contract changes"
  section of the PR template): which endpoint, what changed, and why that is intended. Reviewers
  read the `.ambr` diff as an API diff.
- Never run `--snapshot-update` on the whole suite to "make CI green".

## Layout

| File | Covers |
|---|---|
| `test_campaigns.py` | `/campaigns` CRUD, `/campaigns/{id}/stats`, `/campaigns/parse-jd` |
| `test_candidates.py` | candidate list (every filter, sort, pagination), CSV export, detail, category / notes / pipeline updates, `/resumes/{id}/candidate`, bulk pipeline, bulk delete, delete |
| `test_resumes.py` | upload (accepted, not a PDF, too large, too many files, duplicates), resume list / detail, retry-failed |
| `test_sse.py` | `/campaigns/{id}/stream`: event payload for each resume status; only the requested campaign's events are delivered (B3, fixed in E0-21) |
| `test_stats_health.py` | `/stats`, `/health`, `/health/ready`, `/api/v1/health`, OpenAPI schema |
| `__snapshots__/*.ambr` | the recorded snapshots (syrupy's Amber format) |
| `snapshot_utils.py` | masking serializer and helpers that turn responses into plain data |
| `sse.py` | reads a bounded number of SSE events over raw ASGI, with a timeout |

Test data comes from the factories in `tests/factories.py` (since the read switch, E0-19, a
"candidate" is built as a Person, an Application and its resume's extraction snapshot, so these
tests run the new read path); Redis, Gemini, MinIO and Celery are
faked by the autouse fixtures in `tests/conftest.py`. No test touches the network.

## Masking

Some values change on every run. The `snapshot` fixture (see `conftest.py`) uses
`MaskedAmberExtension`, which replaces them before serializing:

| Value | Placeholder |
|---|---|
| UUIDs, as `uuid.UUID` or anywhere inside a string (ids, object keys, file names, Celery task ids) | `<uuid:1>`, `<uuid:2>`, ... |
| datetimes, as `datetime` or an ISO-8601 timestamp inside a string | `<datetime>` |

UUID placeholders are numbered by **first appearance within the test**, and the same UUID keeps
its number across all snapshot assertions of that test. So relationships stay visible: a resume id
in the upload response is the same `<uuid:3>` as in the stored object key and the Celery call.
Dict keys are visited in sorted order, which is the order the Amber file shows them in.

Everything else must be deterministic by construction:

- Factories use constant defaults and explicit timestamps (Postgres `now()` is frozen inside a
  test's transaction, so server-default timestamps would tie and make `ORDER BY` ambiguous).
- Lists that come from an unordered query are sorted by a stable key in the test.
- The few run-dependent values that are not UUIDs or datetimes (a Python `set` joined into an
  error message, the fake Redis client's error text) are normalised in the test that records them,
  with a comment saying why.

File hashes need no masking: they are SHA-256 of fixed test content.
