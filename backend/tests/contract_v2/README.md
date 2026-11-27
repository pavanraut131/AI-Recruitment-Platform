# Contract tests: `/api/v2`

Snapshot tests that record what every `/api/v2` endpoint returns: status codes, bodies, the
relevant headers, the CSV export, the SSE events, the problem details of every error, and the
whole v2 OpenAPI document (`/api/v2/openapi.json`, the source of the generated TypeScript client).
Same pattern as the v1 characterization tests ([`../characterization`](../characterization/README.md)):
syrupy Amber snapshots through the same masking serializer (`snapshot_utils.MaskedAmberExtension`),
the same factories and fakes.

The difference: v1's snapshots record **current** behaviour (bugs included); these record the
**intended** v2 contract (EPIC-01 spec §6). The same rules apply:

- A failing snapshot means the v2 API changed. Update it only on purpose
  (`--snapshot-update`, for the tests you meant to change), and explain each change in the PR's
  "Snapshot / contract changes" section. An API change also regenerates the client (E1-03).
- Never run `--snapshot-update` on the whole suite to make CI green.

| File | Covers |
|---|---|
| `test_campaigns.py` | list (filters, paging, 422s), create (validation), detail, update, delete, stats, parse-jd |
| `test_applications.py` | campaign list (every filter and sort, ties, 422s), CSV export (filters, selection), detail, update, delete, bulk stage / reject / delete (partial not-found, 422s) |
| `test_uploads.py` | upload (accepted / rejected, 404, too many files, bodies over the parsing limits), resume list, retry-failed, the typed SSE stream (campaign-filtered, no DB transaction held while streaming) |
| `test_people.py` | talent list and search (name / email), Person profile with applications and identity-conflict flags |
| `test_dashboard.py` | `/stats`, `/home` (and its bounds) |
| `test_identity.py` | `/auth/csrf`, `/auth/login` (success, CSRF, 401, 403, 422, 429), `/auth/logout`, `/me` (profile, password, active agency), `/agency`, the 401 that clears an ended session's cookie (real cookies, `tests/identity/conftest.py`; tokens, cookie values and `Retry-After` masked) |
| `test_team.py` | `/team/members` (list, role and client changes, deactivate, reactivate) and `/team/invitations` (list, create, resend, revoke), with the 403 / 404 / 409 / 422 / 429 problems and the last-owner guard (E2-07) |
| `test_auth_tokens.py` | invitation preview and accept, password reset request and confirm, and the emails they send (tokens in links and the expiry date masked) (E2-07) |
| `test_errors_and_openapi.py` | problem details for framework errors (404 route, 405, malformed JSON, 500), v1 errors unchanged, the OpenAPI document and its conventions (operation ids, tags, problem responses, enums, security schemes) |
| `test_query_counts.py` | statements per request are fixed for every list and aggregate (no N+1), the team lists included |
| `data.py` | shared test data (`ranked_campaign`, people helpers) and the statement counter |

Data is deterministic by construction, as in v1: explicit timestamps (`tests.factories.at`), and
fixed application ids where a sort ties (`ranked_campaign`), since ties are broken by id.
