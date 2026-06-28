# ResumeAI — Feature Plan
*Grounded in a full read of the codebase: FastAPI routes, SQLAlchemy models, Celery workers, Gemini prompts, and the Next.js frontend.*

---

## How the system works today (quick orientation)

| Layer | What exists |
|---|---|
| **DB Models** | `Campaign`, `Resume`, `Candidate` (PostgreSQL via SQLAlchemy async) |
| **Candidate fields** | `score`, `category`, `pipeline_stage`, `notes`, `strengths`, `weaknesses`, `missing_skills`, `recommendation`, `summary`, `skills`, `work_experience`, `education`, `projects`, `certifications` |
| **API** | `/campaigns`, `/resumes` (upload + SSE stream), `/candidates` (list, filter, export CSV, update category/pipeline/notes, delete), `/campaigns/parse-jd` |
| **Workers** | `extract_queue` (PDF → text via pdfplumber/PyMuPDF) → `ai_queue` (text → Gemini JSON scoring) |
| **Gemini** | Two prompts: `EXTRACTION_PROMPT` (structured info) + `SCORING_PROMPT` (score 0-100, category, strengths, weaknesses, recommendation) |
| **Frontend** | Dashboard → Campaign list → Campaign detail (UploadZone + CandidateTable + StatsPanel) → Candidate detail page |
| **Scoring rule** | ≥75 = strong_match, ≥50 = moderate_match, ≥25 = weak_match, <25 = rejected |

---

## Phase 1 — Quick Wins (High value, Low effort)

These require minimal backend changes and mostly improve the existing UX.

---

### 1.1 — Score Breakdown / Explainable AI

**Problem**: The score (`87/100`) is currently a black box. Recruiters can't trust a number they can't explain.

**What to change:**
- **`gemini.py` → `SCORING_PROMPT_TEMPLATE`**: Add a `score_breakdown` field to the JSON response:
  ```json
  "score_breakdown": {
    "skills_match": 35,
    "experience_match": 25,
    "project_relevance": 15,
    "education_bonus": 7,
    "communication_quality": 5
  }
  ```
- **`schemas/candidate.py`**: Add `score_breakdown: Optional[dict]` to `ScoringResult` and `CandidateDetail`.
- **`models/candidate.py`**: Add `score_breakdown = mapped_column(JSONB, nullable=True)`.
- **`workers/ai_task.py`**: Save `score_breakdown` to the DB.
- **Frontend `candidates/[id]/page.tsx`**: Render a simple horizontal bar chart or icon grid showing the five sub-scores. No new library needed — pure CSS bars.

**Effort**: ~4 hours | **Value**: 🔥🔥🔥🔥🔥

---

### 1.2 — Custom Score Weights per Campaign

**Problem**: A "Data Engineer" campaign should weight SQL/Spark higher than Python. Today all campaigns use Gemini's own judgment.

**What to change:**
- **`models/campaign.py`**: Add `skill_weights: Mapped[dict] = mapped_column(JSONB, default=dict)` — e.g. `{"Python": 30, "FastAPI": 20, "AWS": 15}`.
- **`schemas/campaign.py`**: Add `skill_weights` to `CampaignCreate` and `CampaignUpdate`.
- **`gemini.py` → `SCORING_PROMPT_TEMPLATE`**: Inject the weights into the prompt context:
  ```
  Skill Weights: Python=30pts, FastAPI=20pts (max 100 total)
  ```
- **Frontend `campaigns/new/page.tsx`**: Add a weight slider (0–50) next to each required skill tag when creating/editing a campaign.

**Effort**: ~5 hours | **Value**: 🔥🔥🔥🔥

---

### 1.3 — Candidate Comparison View

**Problem**: There is no way to view two or three shortlisted candidates side-by-side to make a final call.

**What to change:**
- **Frontend only** — no backend changes needed.
- **`CandidateTable.tsx`**: Add a checkbox column. When 2–3 candidates are checked, show a floating "Compare" button.
- **New page `candidates/compare/page.tsx`**: Reads `ids[]` from query params, fetches each via existing `getCandidate(id)`, and renders a 3-column table — one column per candidate — showing Score, Skills matched, Missing Skills, Strengths, Weaknesses.

**Effort**: ~4 hours | **Value**: 🔥🔥🔥🔥

---

### 1.4 — Retry Quota-Exceeded Resumes Button in UI

**Problem**: The `POST /campaigns/{id}/resumes/retry-failed` API endpoint already exists, but there is no button in the frontend to trigger it.

**What to change:**
- **Frontend `campaigns/[id]/page.tsx`**: When `stats.total_quota_exceeded > 0`, show a banner — *"X resumes hit API quota. [Retry Now →]"* — that calls `retryFailedResumes(campaignId)` (already in `api.ts`).

**Effort**: ~1 hour | **Value**: 🔥🔥🔥🔥🔥

---

## Phase 2 — Core Value Features (Medium effort, High market value)

---

### 2.1 — Natural Language Candidate Search (RAG-based)

**Problem**: Recruiters can currently filter by category, score, and pipeline stage — but they think in sentences, not dropdowns.  
E.g.: *"Find candidates who have used Kubernetes in production and have more than 3 years experience."*

**What to change:**
- **New API route `POST /campaigns/{id}/candidates/nl-search`**:
  - Accepts `{ "query": "..." }`.
  - Uses Gemini to convert the NL query to a set of structured filters (`min_score`, `skills`, `min_experience`).
  - Fetches matching candidates and returns them.
  - Alternatively: embed all candidate `summary` fields using `text-embedding-004`, store in pgvector, and do cosine similarity search.
- **`models/candidate.py`**: Add `embedding: Mapped[list] = mapped_column(Vector(768), nullable=True)` (requires `pgvector` extension + `sqlalchemy-pgvector`).
- **`workers/ai_task.py`**: After scoring, call `genai.embed_content(model="models/text-embedding-004", content=summary)` and save.
- **Frontend `campaigns/[id]/page.tsx`**: Replace the plain search input with an AI Search input that submits to the NL search endpoint. Show a subtle `✦ AI` badge next to the field.

**Effort**: ~10 hours | **Value**: 🔥🔥🔥🔥🔥

---

### 2.2 — Blind Hiring / Anonymous Mode

**Problem**: Names, universities, and locations introduce unconscious bias. A `Candidate Amber-7` approach removes that bias.

**What to change:**
- **`models/campaign.py`**: Add `blind_mode: Mapped[bool] = mapped_column(Boolean, default=False)`.
- **`schemas/campaign.py`**: Expose `blind_mode` in create/update.
- **`schemas/candidate.py`**: Add a `CandidateListItemBlind` response model that replaces `name` with a codename (e.g., `Candidate #4`), nulls out `email`, `phone`, `github_url`, `linkedin_url`.
- **`api/v1/candidates.py`**: In `list_candidates`, check `campaign.blind_mode` and return the blinded schema if enabled.
- **Frontend**: In the campaign detail page, show a toggle — *"Blind Screening"*. When on, mask personal info in both `CandidateTable` and the detail view.

**Effort**: ~6 hours | **Value**: 🔥🔥🔥🔥

---

### 2.3 — Email / URL Resume Intake

**Problem**: Recruiter has to manually download PDFs and re-upload. A shareable intake link removes this friction.

**What to change:**
- **`models/campaign.py`**: Add `intake_token: Mapped[str] = mapped_column(String(64), unique=True, default=lambda: secrets.token_urlsafe(24))`.
- **New API route `POST /intake/{token}`**: Public, no auth. Accepts PDF upload. Finds campaign by token, calls existing `bulk_upload_resumes` logic.
- **Frontend**: In campaign settings, show a shareable link: `https://app.resumeai.co/intake/{token}`. Clicking generates a copyable URL.
- **Public intake page `app/intake/[token]/page.tsx`**: A minimal drag-and-drop page (no sidebar, no auth) where candidates or third-parties drop their PDFs.

**Effort**: ~8 hours | **Value**: 🔥🔥🔥🔥🔥

---

### 2.4 — Campaign Archive & Status Lifecycle

**Problem**: `CampaignStatus` already has `active`, `closed`, `archived` as an enum in the model, but the UI doesn't expose `closed` or `archived` status — it's always shown as `active`.

**What to change:**
- **Frontend `campaigns/page.tsx`**: In the `MoreHorizontal` context menu, add "Close Campaign" and "Archive" options that call `updateCampaign(id, { status: 'closed' })`.
- **Frontend `campaigns/page.tsx`**: Add status filter tabs: `All | Active | Closed | Archived`.
- **`api/v1/campaigns.py` `list_campaigns`**: Add an optional `?status=` query parameter filter.

**Effort**: ~3 hours | **Value**: 🔥🔥🔥

---

## Phase 3 — Power User Features (Higher effort, Competitive moat)

---

### 3.1 — Candidate Activity Log / Audit Trail

**Problem**: When a recruiter moves a candidate from "Screened" to "Offer", there is no record of who did what and when.

**What to change:**
- **New DB model `CandidateActivity`**:
  ```python
  class CandidateActivity(Base):
      id, candidate_id, action, old_value, new_value, actor, created_at
  ```
- **`api/v1/candidates.py`**: After every `PUT`/`PATCH` (category, pipeline, notes), insert a `CandidateActivity` row.
- **New route `GET /candidates/{id}/activity`**: Returns the log.
- **Frontend `candidates/[id]/page.tsx`**: Add a collapsible "Activity" section at the bottom of the candidate profile showing a timeline: *"Pipeline moved Screened → Phone Call — 2 hours ago"*.

**Effort**: ~8 hours | **Value**: 🔥🔥🔥🔥

---

### 3.2 — Campaign Templates

**Problem**: Recruiters at the same company post similar roles repeatedly. They have to re-enter skills every time.

**What to change:**
- **New DB model `CampaignTemplate`**: `id`, `title`, `role`, `required_skills`, `skill_weights`, `description`, `created_at`.
- **`api/v1/campaigns.py`**: Add `POST /campaign-templates` and `GET /campaign-templates`.
- **`api/v1/campaigns.py`**: Add `POST /campaigns?from_template={template_id}` to create a campaign pre-filled from a template.
- **Frontend**: In the "New Campaign" page, show a *"Start from Template"* modal with saved templates listed. One click populates all fields.

**Effort**: ~7 hours | **Value**: 🔥🔥🔥🔥

---

### 3.3 — Bulk Pipeline Actions

**Problem**: If a recruiter wants to reject all "Weak Match" candidates, they have to click each one individually.

**What to change:**
- **`api/v1/candidates.py`**: Add `PATCH /campaigns/{id}/candidates/bulk-pipeline` — accepts `{ "candidate_ids": [...], "stage": "rejected_manual" }`.
- **`CandidateTable.tsx`**: Add a header checkbox for select-all. When rows are selected, show a floating action bar: *"X selected → Move to: [stage dropdown] | [Delete]"*.

**Effort**: ~5 hours | **Value**: 🔥🔥🔥🔥🔥

---

### 3.4 — JSON / PDF Report Export

**Problem**: The CSV export is useful for data, but a formatted PDF report is what a hiring manager or client actually wants to share.

**What to change:**
- **Backend**: Add `GET /campaigns/{id}/report` using `weasyprint` or `reportlab` to generate a campaign summary PDF — cover page, score distribution chart (SVG), and a top-N candidate shortlist table.
- **Frontend `campaigns/[id]/page.tsx`**: Add "Download Report" button next to "Export CSV".

**Effort**: ~8 hours | **Value**: 🔥🔥🔥🔥

---

## Phase 4 — Enterprise & Monetization Layer

---

### 4.1 — Multi-User Auth (JWT)

**What to change:**
- Integrate `fastapi-users` with JWT + PostgreSQL backend.
- Add `User` model with `team_id`.
- All resources (Campaign, Candidate) get a `owner_id` FK.
- Frontend gets a login/register page and stores JWT in `httpOnly` cookie.

**Effort**: ~16 hours | **Value**: Required for B2B

---

### 4.2 — Team Workspaces

- Add `Organization` model. Campaigns/candidates belong to an org.
- Add `OrganizationMember` with roles: `admin`, `recruiter`, `viewer`.
- Only `admin` can delete campaigns or change campaign settings.

**Effort**: ~12 hours | **Value**: Required for B2B

---

### 4.3 — Webhook Outbound Notifications

**What to change:**
- Add `CampaignWebhook` model: `url`, `secret`, `events[]` (e.g., `candidate.processed`, `candidate.pipeline_changed`).
- After each Celery task completes, fire a signed `HMAC-SHA256` POST to the webhook URL.
- This enables ATS integrations (Greenhouse, Lever) without building native connectors first.

**Effort**: ~8 hours | **Value**: 🔥🔥🔥🔥🔥 (Enterprise unlock)

---

## Feature Priority Summary

| # | Feature | Phase | Effort | Market Value |
|---|---|---|---|---|
| 1.4 | Retry quota button (UI only) | 1 | 1h | 🔥🔥🔥🔥🔥 |
| 1.1 | Score breakdown / XAI | 1 | 4h | 🔥🔥🔥🔥🔥 |
| 1.3 | Candidate comparison view | 1 | 4h | 🔥🔥🔥🔥 |
| 3.3 | Bulk pipeline actions | 3 | 5h | 🔥🔥🔥🔥🔥 |
| 1.2 | Custom skill weights | 1 | 5h | 🔥🔥🔥🔥 |
| 2.4 | Campaign status lifecycle | 2 | 3h | 🔥🔥🔥 |
| 2.2 | Blind hiring toggle | 2 | 6h | 🔥🔥🔥🔥 |
| 3.2 | Campaign templates | 3 | 7h | 🔥🔥🔥🔥 |
| 2.3 | Email / URL intake | 2 | 8h | 🔥🔥🔥🔥🔥 |
| 3.1 | Candidate activity log | 3 | 8h | 🔥🔥🔥🔥 |
| 3.4 | PDF report export | 3 | 8h | 🔥🔥🔥🔥 |
| 2.1 | NL search (RAG) | 2 | 10h | 🔥🔥🔥🔥🔥 |
| 4.1 | Auth (JWT) | 4 | 16h | Required for B2B |
| 4.2 | Team workspaces | 4 | 12h | Required for B2B |
| 4.3 | Outbound webhooks | 4 | 8h | 🔥🔥🔥🔥🔥 |

> **Recommended starting point**: Do **1.4 → 1.1 → 3.3** first. Together they take ~10 hours and turn a good demo into a product that recruiters will actually adopt day-to-day.
