# HireFlow AI — Product Requirements Document
### Full-Cycle Hiring Intelligence Platform
**Version:** 1.0 | **Date:** June 2026 | **Status:** Draft for Review

---

## Executive Summary

HireFlow AI evolves from a recruiter-only resume parser into a **full-cycle, multi-stakeholder hiring intelligence platform**. It automates every stage of the hiring funnel — from resume ingestion to offer letter — while giving each stakeholder (recruiter, hiring manager, technical interviewer, candidate) a tailored experience within a single product.

**Market Opportunity:** The global ATS market is valued at ~$7.4B in 2025 and projected to reach $15.5B by 2035 (7.6% CAGR). Over 79% of companies have integrated AI directly into their ATS, and 70% of organizations report reduced time-to-hire after automation. Yet the dominant tools (Workday, SuccessFactors) are expensive, enterprise-only, and fragmented — they require 3–6 additional point solutions to run a complete hiring pipeline. HireFlow AI captures the SMB and mid-market gap with an affordable, unified, AI-first platform.

---

## 1. Problem Statement

### Current Pain Points (Validated Across Hiring Process)

| Stakeholder | Pain Point |
|---|---|
| **Recruiter** | Manually screens hundreds of resumes; no automation beyond basic scoring |
| **Hiring Manager** | No visibility into pipeline until shortlist arrives (often too late) |
| **Technical Interviewer** | Scheduling burden; no structured question set; evaluation is ad hoc |
| **Candidate** | Black box — no feedback, no status updates, drops off mid-funnel |
| **HR / Legal** | No audit trail; inconsistent evaluation = compliance risk |

### What the Current Product Covers
- ✅ Resume bulk upload + AI parsing (Gemini 1.5 Flash)
- ✅ Campaign management per role
- ✅ Candidate scoring & categorization
- ✅ Basic CRM (notes, stage tracking, category override)
- ✅ Real-time SSE progress streaming

### What It's Missing (This PRD)
- ❌ Candidate-facing portal (application, self-screening, async video)
- ❌ Automated pre-screening questionnaire ("how many years in React?")
- ❌ AI-driven async mini technical interview
- ❌ Hiring manager collaboration workspace
- ❌ Live interview coordination (scheduling, structured scorecards)
- ❌ Offer management & onboarding handoff
- ❌ Multi-role permission model

---

## 2. Vision & Goals

**Vision:** Become the operating system of hiring — where every action by every stakeholder is connected, automated where possible, and human-assisted where it matters.

**Product Goals (12-month horizon):**
1. Reduce average time-to-hire by 60% vs. manual process
2. Eliminate 80% of recruiter administrative work in top-of-funnel
3. Provide structured, bias-reduced evaluation at every stage
4. Deliver a candidate experience that generates positive reviews even from rejected applicants
5. Enable teams of 2–500 to run enterprise-quality hiring pipelines

---

## 3. Target Users & Personas

### 3.1 Primary Personas

**Priya — Recruiter / Talent Acquisition**
- Manages 5–15 open roles simultaneously
- Receives 50–300 resumes per role
- Spends 60% of time on scheduling and email coordination
- Success metric: time-to-qualified-shortlist

**Rahul — Hiring Manager (Engineering Lead)**
- Cares about technical fit, team dynamics
- Currently receives a shortlist PDF and schedules interviews manually
- Wants visibility into the funnel without doing recruiter's job
- Success metric: quality of hire after 90 days

**Ananya — Technical Interviewer (Senior Engineer)**
- Pulled into the process 3–4 times per week
- No standardized questions; reinvents process each time
- Wants to see candidate's prior assessment before their interview
- Success metric: time spent per interview cycle

**Arjun — Candidate (Software Engineer)**
- Applies to 20+ roles; rarely hears back
- Frustrated by generic automated rejections
- Values transparency and speed
- Success metric: clarity of process, quality of feedback

**Meera — HR / Compliance Officer**
- Needs defensible, audited decisions
- Needs GDPR/data consent records
- Success metric: zero compliance violations; complete audit logs

### 3.2 Secondary Personas
- **Agency Recruiter** — manages placements for multiple clients; needs campaign isolation
- **Startup Founder** — is all stakeholders in one; needs solo mode

---

## 4. The Full Hiring Lifecycle (Process Map)

```
STAGE 1: SOURCING & INTAKE
  └── Job posting published → Candidates apply via portal
  └── Bulk resume import (existing feature)
  └── Referral submission

STAGE 2: AUTOMATED SCREENING
  └── AI resume scoring vs. campaign criteria (existing)
  └── Auto-disqualify below threshold (configurable)
  └── Auto-invite qualified candidates to: Pre-Screening Questionnaire

STAGE 3: PRE-SCREENING QUESTIONNAIRE (NEW)
  └── Recruiter-configured typed questions
     ├── Quantitative: "Years of experience in React?" → auto-scored
     ├── Multiple choice: "Are you open to relocation?" → filter
     └── Short text: "Describe your largest project scope" → AI-reviewed
  └── Candidate completes async via portal (no scheduling needed)
  └── AI evaluates + scores responses → ranked shortlist

STAGE 4: ASYNC MINI TECHNICAL SCREEN (NEW)
  └── For tech roles: 10-min async challenge
     ├── MCQ technical quiz (auto-graded)
     ├── Short coding problem (auto-executed, auto-graded)
     └── Or: Text-based scenario question (AI-graded rubric)
  └── Results appended to candidate profile

STAGE 5: ASYNC VIDEO SCREEN (NEW)
  └── 3–5 questions, candidate records 1–2 min response each
  └── AI transcribes + scores against rubric
  └── Recruiter reviews AI summary + video; approves/rejects

STAGE 6: HIRING MANAGER SHORTLIST REVIEW (NEW)
  └── Recruiter promotes candidates to HM Review
  └── Hiring manager sees: score, video summary, assessment, AI recommendation
  └── HM approves/declines/requests more info with comments
  └── Async collaboration — no meeting required

STAGE 7: LIVE INTERVIEW COORDINATION (NEW)
  └── System auto-proposes time slots (calendar integration)
  └── Candidate self-schedules from available slots
  └── Structured scorecard auto-generated for each interviewer
     └── Based on campaign skills + candidate gaps identified in prior stages
  └── Interviewers complete scorecard post-interview in platform

STAGE 8: DEBRIEF & DECISION (NEW)
  └── All scorecards aggregated
  └── AI-generated debrief summary: consensus, outliers, recommendation
  └── Hiring manager makes final call in platform

STAGE 9: OFFER & ONBOARDING HANDOFF (NEW)
  └── Offer generated from template with salary / role details
  └── Candidate accepts/declines in portal
  └── On acceptance: onboarding checklist triggered; handoff to HRIS

STAGE 10: ANALYTICS & CONTINUOUS IMPROVEMENT
  └── Time-per-stage tracking
  └── Drop-off analysis
  └── Interviewer calibration (are scores consistent?)
  └── Source quality (which job board yields best hires?)
```

---

## 5. Feature Specifications

### 5.1 Module: Candidate Portal (Public-Facing)

**Overview:** A white-label candidate-facing web application where candidates apply, track status, complete assessments, and receive updates.

**Features:**
- Branded job listing page per campaign (logo, company name, role details)
- Resume upload or LinkedIn profile import
- Consent capture (GDPR-compliant; explicit data usage checkbox)
- Real-time status tracker ("Your application is under review", "Assessment ready", "Interview scheduled")
- Push / email notifications at each stage transition
- Rejection with optional AI-generated personalized feedback ("Your React experience was strong, but the role required 3+ years of system design…")

**Acceptance Criteria:**
- Mobile-first responsive design
- Loads in < 2 seconds on 3G
- Candidate can delete their own data (right to erasure)
- All interactions timestamped and stored in audit log

---

### 5.2 Module: Pre-Screening Questionnaire Builder

**Overview:** Recruiters configure a set of questions that are automatically sent to candidates who pass the AI resume screen. Responses are automatically scored and ranked.

**Question Types:**

| Type | Description | Scoring |
|---|---|---|
| Numeric | "How many years of React experience?" | Rule-based: ≥3 = pass |
| Boolean | "Are you authorized to work in India?" | Hard filter |
| Multiple Choice | "Preferred work mode: remote / hybrid / onsite" | Preference matching |
| Short Text (AI) | "Describe a system you designed under pressure" | AI rubric score 0–10 |
| File Upload | "Share a GitHub link or portfolio" | Manual / AI review |

**Recruiter Configurator:**
- Drag-and-drop question builder
- Set required vs. optional questions
- Set threshold: "Auto-advance if total score ≥ 70%"
- Set deadline: "Questionnaire expires in 5 days"
- Preview mode (see what candidate sees)

**AI Auto-Scoring for Text Questions:**
- Recruiter writes evaluation rubric in natural language ("Looking for: clear communication, evidence of impact, technical depth")
- Gemini scores each response against rubric
- Score + reasoning displayed to recruiter; recruiter can override

**Candidate Experience:**
- Invitation email with deadline and time estimate
- Save-and-resume (no losing progress)
- Progress indicator
- Confirmation screen after submission

---

### 5.3 Module: Async Technical Assessment

**Overview:** A 10–15 minute technical evaluation sent to candidates after pre-screening. Removes the need for a scheduled phone screen for early-stage technical filtering.

**Assessment Types:**

**A. MCQ Quiz**
- Recruiter selects from pre-built question bank (Python, React, SQL, System Design, etc.)
- Or generates questions via AI: "Generate 10 intermediate React questions for a frontend role"
- Auto-graded with score and time-per-question analytics
- Anti-cheating: question randomization, timer per question, tab-switch detection

**B. Coding Challenge**
- Embedded code editor (Monaco Editor)
- Candidate writes code; test cases auto-run on submission
- Supported languages: Python, JavaScript, Java, TypeScript, Go
- Output: pass/fail per test case, time complexity, edge case handling

**C. Scenario / Case Question**
- Text-based problem: "Given this requirement, how would you architect the backend?"
- AI evaluates against structured rubric
- Recruiter reviews AI verdict + can comment

**Configuration:**
- Mix and match types within one assessment
- Time limits per section (enforced)
- Score weightings per question type
- Pass threshold → auto-advance to next stage

---

### 5.4 Module: Async Video Screen

**Overview:** Candidates record short video responses to role-specific questions. AI transcribes, analyzes, and scores. Replaces phone screens entirely for many roles.

**How It Works:**
1. Recruiter selects or generates 3–5 video questions
2. Candidate receives invitation link (no account required)
3. Candidate records responses in browser (up to 2 minutes per question)
4. System processes: transcription → AI analysis → score report

**AI Analysis Dimensions:**
- Content relevance to question
- Structured communication (intro / point / example / conclusion)
- Technical accuracy of claims
- Enthusiasm and confidence (optional; recruiter-toggleable due to bias concerns)
- Red flags (contradictions with resume, vague claims)

**Recruiter View:**
- AI summary card per candidate with key quotes
- Option to watch full video
- One-click approve / decline / hold
- Side-by-side comparison of top candidates

**Candidate Safeguards:**
- Unlimited practice runs before submitting
- Retake option (configurable: 0–3 retakes)
- Clear instructions: "AI will transcribe and summarize your response; a human recruiter will review before any decision"

---

### 5.5 Module: Hiring Manager Workspace

**Overview:** A purpose-built view for hiring managers — not the recruiter's CRM, but a focused review board where they see only what's needed to make decisions.

**Views:**
- **Shortlist Board**: Cards for each promoted candidate with AI summary, score, video thumbnail, assessment grade
- **Comparison Mode**: Side-by-side view of 2–4 candidates
- **Decision Panel**: Approve / Request Info / Decline — with mandatory comment
- **Activity Feed**: See recruiter notes, stage changes, candidate messages (read-only unless commenting)

**Collaboration Features:**
- HM can tag specific interviewers ("Assign Ananya for system design round")
- HM can leave questions for recruiter ("Does this person have team lead experience?")
- @mentions with email notification
- Decision history with timestamps (full audit)

**HM-Specific Analytics:**
- Average score of candidates presented vs. those hired (calibration metric)
- How long it takes HM to review a shortlist (recruiter uses this for SLA)

---

### 5.6 Module: Interview Scheduling & Coordination

**Overview:** AI-assisted scheduling that eliminates the back-and-forth email coordination that consumes ~40% of recruiter time.

**Flow:**
1. Hiring manager approves candidate for live interview
2. System pulls available slots from connected calendars (Google Calendar / Outlook)
3. Candidate receives self-scheduling link; picks from available slots
4. System auto-creates calendar invites for all parties
5. System auto-generates interview brief for each interviewer (candidate profile + their specific focus area + suggested questions)
6. 24h reminder sent to candidate + interviewer

**Structured Scorecard (Auto-Generated):**
- Based on campaign skills matrix + gaps identified in prior stages
- Sections: Technical Skills, Problem Solving, Communication, Culture Fit, Red Flags
- Rating: 1–5 per dimension + free text
- Overall: Strong Yes / Yes / No / Strong No
- Submitted in platform immediately after interview ends

**Conflict Detection:**
- If interviewer cancels < 4h before, system alerts recruiter and suggests replacement
- Candidate no-show tracking (impacts candidate rating)

---

### 5.7 Module: Debrief & Decision Engine

**Overview:** After all interviews are complete, the system aggregates scorecards and generates an AI-assisted debrief to help the panel make a faster, better-calibrated decision.

**Debrief Summary (AI-Generated):**
- Consensus view: where all interviewers agreed
- Outlier highlights: where one interviewer rated significantly differently
- Strength/weakness aggregation across all rounds
- Recommendation: Hire / Hold / Pass — with confidence level
- Risk flags: "Two interviewers raised communication concerns"

**Decision Flow:**
- Hiring manager sees debrief → makes final decision
- Decision logged with reason (required for compliance)
- If "Hire": triggers offer workflow
- If "No": candidate notified with optional feedback

---

### 5.8 Module: Offer Management

**Overview:** Light offer workflow that closes the loop within the platform before handoff to HRIS.

**Features:**
- Offer letter template (customizable per campaign)
- Auto-fill: candidate name, role, salary, start date, location
- Approval flow: HM drafts → HR approves → candidate receives
- Candidate portal: view offer, e-sign or decline with reason
- Expiry countdown visible to both recruiter and candidate
- On accept: generate onboarding task list, trigger HRIS webhook (if integrated)

---

### 5.9 Module: Multi-Stakeholder Permissions

**Role → Access Matrix:**

| Feature | Super Admin | Recruiter | Hiring Manager | Interviewer | Candidate |
|---|---|---|---|---|---|
| Create Campaigns | ✅ | ✅ | ❌ | ❌ | ❌ |
| View All Candidates | ✅ | ✅ | Shortlisted Only | Assigned Only | Self Only |
| Override AI Score | ✅ | ✅ | ❌ | ❌ | ❌ |
| Approve Shortlist | ✅ | ❌ | ✅ | ❌ | ❌ |
| Submit Scorecard | ✅ | ❌ | ✅ | ✅ | ❌ |
| Make Offer | ✅ | With Approval | With Approval | ❌ | ❌ |
| View Analytics | ✅ | Own Campaigns | Own Roles | ❌ | ❌ |
| Manage Users | ✅ | ❌ | ❌ | ❌ | ❌ |

---

## 6. Technical Architecture (Additions)

### 6.1 New Services

```
Existing Services:
  FastAPI Backend ← PostgreSQL ← Redis ← Celery Workers ← Gemini ← MinIO

New Services to Add:
  ┌─────────────────────────────────────────────┐
  │ Candidate Portal (Next.js — separate app)   │
  │   └── JWT auth (magic link, no password)    │
  │   └── Video Recording (WebRTC → S3/MinIO)   │
  ├─────────────────────────────────────────────┤
  │ Code Execution Service                       │
  │   └── Sandboxed Docker runner per submission│
  │   └── Languages: Python, JS, Java, TS, Go  │
  ├─────────────────────────────────────────────┤
  │ Video Processing Worker (Celery Queue)       │
  │   └── FFmpeg transcoding → MinIO            │
  │   └── Whisper API / Deepgram transcription  │
  │   └── Gemini analysis of transcript         │
  ├─────────────────────────────────────────────┤
  │ Notification Service                         │
  │   └── Email: SendGrid / Resend               │
  │   └── In-app: WebSocket push                │
  ├─────────────────────────────────────────────┤
  │ Calendar Integration Service                 │
  │   └── Google Calendar API                   │
  │   └── Microsoft Graph API (Outlook)          │
  │   └── Availability conflict resolution      │
  └─────────────────────────────────────────────┘
```

### 6.2 New Data Models

**Assessment** — linked to Campaign; holds question set, time limits, pass threshold
**AssessmentSubmission** — linked to Candidate + Assessment; stores answers, scores, timing data
**VideoScreen** — linked to Candidate + Campaign; stores video URLs, transcripts, AI scores
**InterviewSlot** — linked to Candidate + Interviewer; calendar event metadata
**Scorecard** — linked to InterviewSlot + Interviewer; structured ratings per dimension
**Offer** — linked to Candidate + Campaign; template, version, e-sign status

### 6.3 Key API Additions

```
POST   /api/campaigns/{id}/questionnaire          — create questionnaire
POST   /api/campaigns/{id}/assessment             — create technical assessment
GET    /api/candidates/{id}/journey               — full pipeline view

# Candidate Portal (public JWT)
POST   /api/portal/apply/{campaign_slug}          — submit application
POST   /api/portal/questionnaire/{token}          — submit questionnaire
POST   /api/portal/video/{token}/upload           — upload video chunk
GET    /api/portal/status/{token}                 — application status

# Scheduling
POST   /api/interviews/schedule                   — create interview with slot
GET    /api/interviews/{id}/available-slots       — get calendar slots
POST   /api/interviews/{id}/scorecard             — submit scorecard

# Offers
POST   /api/offers                                — create offer
POST   /api/portal/offer/{token}/sign             — candidate e-sign
```

---

## 7. Competitive Analysis

| Feature | HireFlow AI | HireVue | Greenhouse | Workday | Lever |
|---|---|---|---|---|---|
| AI Resume Parsing | ✅ Gemini | ✅ | ✅ | ✅ | ✅ |
| Pre-Screen Questionnaire | ✅ AI-scored | ✅ | ✅ | ✅ | ✅ |
| Async Video Screen | ✅ AI | ✅ (core product) | Via integration | Via integration | Via integration |
| Coding Assessment | ✅ Built-in | Via integration | Via integration | ❌ | Via integration |
| HM Collaboration Workspace | ✅ | ❌ | ✅ | ✅ | ✅ |
| Scheduling Automation | ✅ | ✅ | Via Calendly | ✅ | ✅ |
| AI Debrief Aggregation | ✅ | ❌ | ❌ | ❌ | ❌ |
| Offer Management | ✅ Light | ❌ | ✅ | ✅ | ✅ |
| Single Unified Platform | ✅ | ❌ (video only) | ❌ (ATS only) | ✅ (enterprise $) | ❌ |
| SMB Pricing | ✅ | ❌ ($35K+/yr) | ❌ (mid-market) | ❌ (enterprise) | ❌ (mid-market) |
| Self-hosted / Containerized | ✅ Docker | ❌ | ❌ | ❌ | ❌ |

**Unique Differentiators:**
1. **End-to-end in one platform** — no point-solution integrations needed
2. **AI at every stage** — not just screening; AI assists questionnaire scoring, video analysis, debrief synthesis
3. **Transparent AI** — candidates always know when AI is involved; explainable scores
4. **Self-hostable** — ideal for companies with data residency requirements (healthcare, BFSI, government)
5. **SMB-accessible pricing** — enterprise-quality at startup cost

---

## 8. Monetization & Pricing

### Tier 1: Starter (Free Forever)
- 1 active campaign
- Up to 50 resumes/month
- AI resume scoring
- Basic CRM
- Target: solo recruiters, very early startups

### Tier 2: Growth ($99/month)
- 5 active campaigns
- 500 resumes/month
- Pre-screening questionnaire
- Async video screen (50 videos/month)
- Hiring manager workspace
- Email notifications
- Target: Series A–B startups, SMBs

### Tier 3: Scale ($299/month)
- Unlimited campaigns
- 2,000 resumes/month
- Everything in Growth
- Coding assessments (100/month)
- Calendar scheduling integration
- Scorecard & debrief engine
- Offer management
- Advanced analytics
- Target: Mid-market companies (50–500 employees)

### Tier 4: Enterprise (Custom)
- Unlimited everything
- SSO / SAML
- Custom AI models / rubrics
- Dedicated support
- SLA guarantees
- HRIS integrations (Workday, BambooHR, Darwinbox)
- White-label candidate portal
- Target: Large enterprises, staffing agencies

### Usage-Based Add-Ons
- Extra resumes: $0.05/resume
- Extra video screens: $1.50/video
- Extra coding assessments: $2/assessment
- API access: $0.01/API call

---

## 9. Go-To-Market Strategy

### Phase 1 — SMB Penetration (Months 1–6)
- Target: Indian startups (Series A–B), IT staffing firms
- Channel: Product Hunt launch, LinkedIn content by founders, dev community (Y Combinator India, iSPIRT)
- Message: "Stop using 5 tools to hire one engineer. HireFlow AI does it all."
- Goal: 200 paying customers, $20K MRR

### Phase 2 — Mid-Market Expansion (Months 6–12)
- Target: 100–500 employee tech companies
- Channel: Outbound SDR motion, partnerships with HR consulting firms
- Integrations: Slack (interview notifications), JIRA (sync eng hiring with sprint planning)
- Goal: 50 Scale-tier customers, $90K MRR

### Phase 3 — Enterprise & Global (Year 2)
- Localization: Arabic, Spanish, Bahasa for MENA and SEA expansion
- Compliance: SOC 2 Type II, ISO 27001 certification
- HRIS integrations: BambooHR, Darwinbox, Workday
- Goal: 5 enterprise accounts, $500K+ ARR

---

## 10. Phased Roadmap

### Phase 1 — Foundation (Months 1–2)
Already built or near-complete:
- ✅ Resume bulk upload + AI scoring
- ✅ Campaign management
- ✅ Basic CRM + pipeline stages
- 🔧 Multi-user roles (Recruiter / HM)
- 🔧 Email notification service
- 🔧 Candidate portal (apply + status tracker)

### Phase 2 — Automation Layer (Months 3–4)
- Pre-screening questionnaire builder
- AI scoring of text responses
- Async video screen (record in browser)
- Video transcription + AI analysis
- Auto-advancement rules (score thresholds)

### Phase 3 — Collaboration & Assessment (Months 5–6)
- Hiring manager workspace
- MCQ + coding assessment module
- Interview scheduling (calendar integration)
- Structured scorecard
- @mentions and in-platform messaging

### Phase 4 — Intelligence & Closing (Months 7–8)
- AI debrief aggregation
- Offer management + e-sign
- Advanced analytics dashboard (source quality, funnel drop-off, interviewer calibration)
- HRIS webhook (BambooHR, Darwinbox)

### Phase 5 — Scale & Enterprise (Months 9–12)
- SSO / SAML
- White-label portal
- Custom AI rubric training
- SOC 2 compliance preparation
- Mobile apps (recruiter + candidate)

---

## 11. Success Metrics & KPIs

### Product Health
| Metric | Target (Month 6) | Target (Month 12) |
|---|---|---|
| Active Campaigns | 500 | 2,000 |
| Resumes Processed/Month | 50,000 | 250,000 |
| Avg Time-to-Shortlist | < 48h | < 24h |
| Questionnaire Completion Rate | > 70% | > 80% |
| Video Screen Completion Rate | > 60% | > 70% |

### Business Health
| Metric | Target (Month 6) | Target (Month 12) |
|---|---|---|
| MRR | $20K | $100K |
| Paying Customers | 200 | 1,000 |
| Churn Rate | < 5%/month | < 3%/month |
| NPS | > 40 | > 55 |
| CAC Payback | < 6 months | < 4 months |

### Candidate Experience
| Metric | Target |
|---|---|
| Application → First Response | < 24h |
| Candidate Satisfaction Score | > 4.0/5.0 |
| % Receiving Feedback (incl. rejected) | > 80% |

---

## 12. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| AI hallucination in scoring | Medium | High | Human-in-the-loop override; confidence thresholds; show raw evidence |
| Bias in AI screening | Medium | High | Bias audit quarterly; blind mode option (hide name/photo); explainable scores |
| Candidate data breach | Low | Very High | Encryption at rest + in transit; right-to-erasure; self-hosted option |
| Low video completion rates | High | Medium | Mobile-optimized recorder; retake option; clear candidate instructions |
| Competition from Workday/HireVue SMB moves | Low | High | Speed of innovation; SMB pricing; self-hostable as moat |
| Gemini API cost at scale | Medium | Medium | Caching, deduplication (already built); model tiering for cheaper tasks |
| Legal: AI in hiring regulations (EU AI Act) | Medium | High | Explainability layer; human final decision requirement; compliance mode |

---

## 13. Compliance & Ethics Framework

### EU AI Act Alignment (High-Risk Use Case)
Hiring automation is classified as **high-risk** under the EU AI Act. Compliance requirements:
- Human oversight: AI never makes final hiring decisions alone — always a human approval step
- Explainability: Every AI score shows the factors behind it
- Bias monitoring: Regular audits; ability to flag and report demographic disparities
- Data minimization: Only collect what's needed; no biometric scoring unless explicitly disclosed and consented

### Candidate Rights
- Right to know when AI is used (displayed at every AI-assisted step)
- Right to request human review of AI-rejected application
- Right to data deletion (GDPR / DPDP Act India)
- Right to explanation of rejection decision

### Data Handling
- All PII stored encrypted (AES-256 at rest)
- Resumes auto-deleted after configurable retention period (default: 12 months)
- Video recordings: candidate consent required; auto-deleted after 90 days unless retained explicitly
- Audit log: immutable, retained 3 years

---

## 14. Appendix: Glossary

| Term | Definition |
|---|---|
| Campaign | A hiring drive for one specific role; contains all candidates, settings, and assessments for that role |
| Pre-Screening Questionnaire | Async form sent to resume-qualified candidates; collects experience/preference data before human review |
| Async Video Screen | Recorded video interview where candidate answers questions at their own time; AI analyzes responses |
| Scorecard | Structured evaluation form completed by interviewers after a live interview |
| Debrief | Aggregated view of all scorecards + AI synthesis to support final hiring decision |
| Pipeline Stage | Current status of a candidate: Screened → Phone Call → Technical → Offer → Hired / Rejected |
| Matching Category | AI-assigned tier: Strong Match, Moderate Match, Weak Match, Rejected — based on resume score vs. campaign criteria |
| HM | Hiring Manager |
| SSE | Server-Sent Events — the real-time streaming mechanism used to update the recruiter UI |

---

*Document Owner: Product Team | Next Review: Q3 2026 | Distribution: Internal + Investor Use*
