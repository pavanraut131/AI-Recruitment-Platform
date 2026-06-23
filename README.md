# AI Resume Bulk Import System

> Scalable, AI-powered bulk resume processing and candidate categorization platform built with FastAPI, Celery, Next.js, and Google Gemini.

---

## Architecture

```
Next.js (Frontend) → FastAPI (Backend) → Celery Workers → Gemini AI
                           ↕                    ↕
                       PostgreSQL           MinIO (PDFs)
                           ↕
                         Redis (Queue + SSE pub/sub)
```

## Features

- **Bulk Upload**: Drag-and-drop up to 50 PDF resumes at once
- **AI Extraction**: Name, email, skills, experience, education via Gemini 1.5 Flash
- **Hybrid PDF Parsing**: pdfplumber (native) → PyMuPDF → Gemini Vision (scanned PDFs)
- **Real-time Updates**: Server-Sent Events (SSE) stream live processing status per file
- **AI Scoring**: Each candidate scored 0–100 against job role requirements
- **Smart Categorization**: Strong Match / Moderate Match / Weak Match / Rejected
- **Candidate Dashboard**: Filter by skill, score range, category, search by name
- **Analytics Panel**: Pie chart (category distribution) + Bar chart (top skills)
- **Celery Monitoring**: Flower dashboard at `localhost:5555`
- **Retry Logic**: 3 automatic retries with exponential backoff per failed task

---

## Quick Start

### 1. Prerequisites
- Docker + Docker Compose
- Google Gemini API key ([get one here](https://aistudio.google.com/app/apikey))

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env and set your GEMINI_API_KEY
```

### 3. Start all services
```bash
docker compose up --build
```

### 4. Run database migrations
```bash
# Wait for services to be healthy, then:
docker compose exec backend alembic revision --autogenerate -m "initial"
docker compose exec backend alembic upgrade head
```

### 5. Open the app
| Service | URL |
|---|---|
| Frontend (Next.js) | http://localhost:3000 |
| Backend API Docs | http://localhost:8000/docs |
| Flower (Celery) | http://localhost:5555 |
| MinIO Console | http://localhost:9001 |

---

## Usage Flow

1. **Create Campaign** → set role + required skills (e.g. "AI Engineer", Python, FastAPI, LLMs)
2. **Upload Resumes** → drag & drop up to 50 PDFs
3. **Watch Live Processing** → SSE updates show each file: Extracting → AI Analysis → Done
4. **Review Candidates** → sorted by AI score, filterable by category/skill/experience
5. **View Detail** → full profile with strengths, weaknesses, missing skills, recommendation
6. **Override Category** → manually adjust AI classification if needed

---

## Development (without Docker)

### Backend
```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Start services locally (need redis + postgres + minio running)
uvicorn app.main:app --reload --port 8000

# Worker 1 (PDF extraction)
celery -A workers.celery_app worker -Q extract_queue --concurrency=4 --loglevel=info

# Worker 2 (AI processing)
celery -A workers.celery_app worker -Q ai_queue --concurrency=2 --loglevel=info
```

### Frontend
```bash
cd frontend
npm install
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

---

## Project Structure

```
.
├── backend/
│   ├── app/
│   │   ├── api/v1/           # FastAPI routers
│   │   │   ├── campaigns.py  # Campaign CRUD + stats
│   │   │   ├── resumes.py    # Bulk upload + SSE stream
│   │   │   └── candidates.py # Candidate listing + filters
│   │   ├── core/
│   │   │   ├── config.py     # Pydantic settings
│   │   │   ├── database.py   # Async SQLAlchemy
│   │   │   └── storage.py    # MinIO/S3 abstraction
│   │   ├── models/           # SQLAlchemy ORM models
│   │   ├── schemas/          # Pydantic schemas
│   │   ├── services/
│   │   │   ├── gemini.py     # Gemini extraction + scoring
│   │   │   └── pdf.py        # pdfplumber + PyMuPDF
│   │   └── main.py           # FastAPI app + lifespan
│   ├── workers/
│   │   ├── celery_app.py     # Celery factory + routing
│   │   ├── extract_task.py   # Worker 1: PDF → text
│   │   └── ai_task.py        # Worker 2: text → Gemini → score
│   └── alembic/              # Database migrations
├── frontend/
│   └── src/
│       ├── app/              # Next.js App Router pages
│       │   ├── page.tsx              # Dashboard
│       │   ├── campaigns/page.tsx    # Campaign list
│       │   ├── campaigns/new/page.tsx
│       │   ├── campaigns/[id]/page.tsx  # Upload + Candidates + Stats
│       │   └── candidates/[id]/page.tsx # Full candidate profile
│       ├── components/
│       │   ├── Sidebar.tsx
│       │   ├── UploadZone.tsx    # Drag-and-drop + SSE consumer
│       │   ├── CandidateTable.tsx
│       │   └── StatsPanel.tsx   # Recharts analytics
│       └── lib/api.ts           # Typed API client
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/v1/campaigns` | Create campaign |
| GET | `/api/v1/campaigns` | List all campaigns |
| GET | `/api/v1/campaigns/{id}` | Get campaign + stats |
| POST | `/api/v1/campaigns/{id}/upload` | Bulk upload PDFs |
| GET | `/api/v1/campaigns/{id}/stream` | SSE real-time updates |
| GET | `/api/v1/campaigns/{id}/candidates` | List candidates (with filters) |
| GET | `/api/v1/candidates/{id}` | Full candidate profile |
| PUT | `/api/v1/candidates/{id}/category` | Override category |
| GET | `/api/v1/health` | Health check |

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `GEMINI_API_KEY` | **Required** — Google Gemini API key | — |
| `GEMINI_MODEL` | Gemini model to use | `gemini-1.5-flash` |
| `DATABASE_URL` | Async PostgreSQL URL | — |
| `REDIS_URL` | Redis connection URL | `redis://redis:6379/0` |
| `MINIO_ENDPOINT` | MinIO host | `minio:9000` |
| `MAX_FILE_SIZE_MB` | Max PDF size | `10` |
| `MAX_FILES_PER_BATCH` | Max files per upload | `50` |

---

## Future Improvements (from problem statement)

- [ ] JWT authentication (FastAPI-Users)
- [ ] Semantic search with embeddings (pgvector)
- [ ] Candidate-chat assistant (RAG)
- [ ] Resume duplicate detection
- [ ] Email notifications (SendGrid)
- [ ] Interview scheduling
- [ ] ATS integration
- [ ] Cloud Run / GKE deployment configs
