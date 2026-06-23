
# AI Resume Bulk Import and Intelligent Candidate Categorization System

## Problem Statement

Build a scalable web application that allows recruiters or hiring teams to upload resumes in bulk (approximately 20–50 PDF files per upload batch), automatically extract structured candidate information, classify candidates according to job roles, and provide AI-assisted ranking and insights.

The system should process resumes asynchronously using background workers and use AI models to improve extraction quality, candidate understanding, and role matching.

The application should be designed with modern architecture practices and be suitable as a learning project for scalable cloud-native systems.

---

# Objectives

The system should:

- Support bulk upload of PDF resumes (20–50 PDFs per batch)
- Extract resume content and convert into structured data
- Process files asynchronously
- Classify candidates based on target job roles
- Use AI models for candidate understanding
- Provide searchable and filterable candidate views
- Handle failures and retries
- Follow production best practices

---

# Example Use Case

A recruiter creates a hiring campaign:

Role:

AI Engineer

Required Skills:

- Python
- FastAPI
- Machine Learning
- LLMs
- Vector Databases
- Cloud Platforms

Recruiter uploads:

50 PDF resumes

Expected outcome:

The system:

1. Extracts candidate information
2. Identifies skills and experience
3. Uses AI to understand candidate profiles
4. Scores candidates against role requirements
5. Categorizes candidates:

- Highly relevant
- Medium match
- Low match
- Rejected

6. Displays ranked candidate list

---

# Functional Requirements

## Resume Upload

Features:

- Upload 20–50 PDFs simultaneously
- Validate PDF format
- Restrict unsupported file types
- Handle large file uploads
- Show upload progress
- Support drag-and-drop UI

---

## Resume Extraction

Extract:

- Name
- Email
- Phone number
- Skills
- Education
- Work experience
- Projects
- Certifications
- Github profile
- LinkedIn profile
- Years of experience

Store extracted output in structured format.

Example:

```json
{
  "name":"John Doe",
  "skills":["Python","FastAPI","LangChain"],
  "experience":"4 years"
}
```

---

## AI Processing

Use Gemini for:

### Candidate understanding

Tasks:

- Skill normalization
- Resume summarization
- Role matching
- Candidate scoring
- Candidate strengths
- Missing skills detection

Example output:

Candidate score: 87/100

Strengths:

- Strong backend experience
- LLM exposure
- Cloud experience

Weaknesses:

- Limited production ML experience

Recommendation:

Proceed to technical round

---

## Classification

Categories:

- Strong Match
- Moderate Match
- Weak Match
- Rejected

---

## Search and Filtering

Support:

- Search by skills
- Search by experience
- Search by role
- Search by education
- Candidate score range

---

## Dashboard

Dashboard should show:

- Total resumes uploaded
- Total processed
- Failed processing count
- Average candidate score
- Top skills distribution

---

# Suggested Architecture

Frontend:

React

Backend:

FastAPI

Background Processing:

Celery

Broker:

Redis

Database:

PostgreSQL

Object Storage:

Cloud storage bucket

AI:

Gemini API

Containerization:

Docker

Deployment:

Cloud Run or GKE

---

# Processing Flow

User uploads PDFs

↓

Store PDFs in cloud storage

↓

FastAPI creates processing jobs

↓

Celery worker receives task

↓

Worker extracts PDF text

↓

Worker calls Gemini

↓

Worker structures candidate data

↓

Worker stores processed result

↓

UI displays status and results

---

# Suggested Worker Design

Worker 1:

PDF extraction worker

Responsibilities:

- Read PDFs
- OCR if required
- Extract text
- Clean text

Worker 2:

AI processing worker

Responsibilities:

- Send content to Gemini
- Candidate scoring
- Classification
- Summary generation

---

# Non Functional Requirements

## Performance

- Handle 50 PDFs in a batch
- Processing should not block API requests
- Support concurrent jobs

## Reliability

- Retry failed jobs
- Dead-letter queue support
- Resume failed tasks

## Security

- File validation
- Authentication
- Authorization
- Rate limiting
- Secure API keys

## Monitoring

Track:

- API latency
- Worker processing time
- Queue size
- Error rate

---

# Best Practices

- Use environment variables
- Use Docker containers
- Add structured logging
- Add API versioning
- Add health endpoints
- Add centralized error handling
- Add request tracing
- Add CI/CD

---

# Learning Goals

This project should help learn:

- FastAPI architecture
- Background processing
- Celery workers
- Redis queues
- AI integration
- Resume parsing
- Production system design
- Cloud deployment
- Docker
- Monitoring and observability
- Scaling strategies

---

# Future Improvements

- Semantic search using embeddings
- Candidate-chat assistant
- Resume duplicate detection
- ATS integration
- Email notifications
- Interview scheduling
- Multi-role matching
- Resume analytics dashboard
- RAG-based recruiter assistant

---

# Success Criteria

The system is successful if:

- Bulk uploads work reliably
- PDFs process asynchronously
- Candidate extraction is accurate
- AI classification is useful
- Users can search candidates efficiently
- System remains responsive under load
