import { textPdf } from "./resume-pdf"

/**
 * The hero flow's data. Scores are fixed by the fake AI provider's rules
 * (backend/app/modules/ai/fake.py): skills 40 × share of required skills,
 * experience 5 per year (max 30), projects 5 each (max 15), education 7,
 * writing 3; strong ≥ 75, moderate ≥ 50, weak ≥ 25.
 *
 * Every run gets a fresh id, used in the campaign title and the e-mails, so a
 * run never depends on (or collides with) data from an earlier one.
 *
 * A run that refreshes the README's screenshots (E2E_SCREENSHOT_DIR set) uses
 * friendly demo names instead: no run id in the title, e-mails or file names.
 * It relies on a fresh stack (the default of `make e2e`), where nothing else
 * has those names; `searchTerm` then finds the three people by their domain.
 */
export const PITCH = !!process.env.E2E_SCREENSHOT_DIR

export function newRunId(): string {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`
}

/** The campaign's title. */
export function campaignTitle(runId: string): string {
  return PITCH ? "Senior Python Engineer, platform team" : `E2E ${runId} Python platform`
}

/** What Talent is searched for to find this run's three people (and no one else). */
export function searchTerm(runId: string): string {
  return PITCH ? "@northbank.example" : runId
}

/** The fake reads the first sentence as the role and these five vocabulary skills. */
export const JOB_DESCRIPTION =
  "Senior Python Engineer. 5+ years building APIs with Python, FastAPI, PostgreSQL, Docker and AWS. " +
  "You will own the data platform behind our hiring products and mentor two engineers."
export const EXTRACTED_ROLE = "Senior Python Engineer"
export const EXTRACTED_SKILLS = ["Python", "PostgreSQL", "FastAPI", "Docker", "AWS"]

export type Candidate = {
  name: string
  email: string
  filename: string
  /** The match label the UI shows for the category (strong, moderate, weak). */
  category: "Strong match" | "Possible match" | "Unlikely match"
  score: number
  pdf: Buffer
}

export function heroCandidates(runId: string): Candidate[] {
  const email = (local: string) => (PITCH ? `${local}@northbank.example` : `${local}.${runId}@example.com`)
  const file = (slug: string) => (PITCH ? `${slug}-cv.pdf` : `${slug}-${runId}.pdf`)
  return [
    {
      // 5/5 skills (40) + 8 years (30) + 2 projects (10) + degree (7) + 3 = 90
      name: "Asha Verma",
      email: email("asha.verma"),
      filename: file("asha-verma"),
      category: "Strong match",
      score: 90,
      pdf: textPdf([
        "Asha Verma",
        email("asha.verma"),
        "Senior Software Engineer",
        "8 years of experience building APIs with Python, FastAPI, PostgreSQL, Docker and AWS.",
        "B.Tech in Computer Science",
        "Project: Hiring analytics platform on AWS with Python and PostgreSQL",
        "Project: Event pipeline with FastAPI and Docker",
      ]),
    },
    {
      // 3/5 skills (24) + 4 years (20) + 1 project (5) + degree (7) + 3 = 59
      name: "Rahul Iyer",
      email: email("rahul.iyer"),
      filename: file("rahul-iyer"),
      category: "Possible match",
      score: 59,
      pdf: textPdf([
        "Rahul Iyer",
        email("rahul.iyer"),
        "Backend Developer",
        "4 years of experience with Python, FastAPI and Docker.",
        "B.Sc in Computer Science",
        "Project: Internal tools in Python for the finance team",
      ]),
    },
    {
      // 1/5 skills (8) + 2 years (10) + degree (7) + 3 = 28
      name: "Meera Nair",
      email: email("meera.nair"),
      filename: file("meera-nair"),
      category: "Unlikely match",
      score: 28,
      pdf: textPdf([
        "Meera Nair",
        email("meera.nair"),
        "Junior Data Analyst",
        "2 years of experience with Python and spreadsheet reporting.",
        "Bachelor of Commerce",
      ]),
    },
  ]
}
