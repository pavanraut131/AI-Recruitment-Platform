import { textPdf } from "./resume-pdf"

/**
 * The hero flow's data. Scores are fixed by the fake AI provider's rules
 * (backend/app/modules/ai/fake.py): skills 40 × share of required skills,
 * experience 5 per year (max 30), projects 5 each (max 15), education 7,
 * writing 3; strong ≥ 75, moderate ≥ 50, weak ≥ 25.
 *
 * Every run gets a fresh id, used in the campaign title and the e-mails, so a
 * run never depends on (or collides with) data from an earlier one.
 */
export function newRunId(): string {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`
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
  category: "Strong match" | "Moderate match" | "Weak match"
  score: number
  pdf: Buffer
}

export function heroCandidates(runId: string): Candidate[] {
  const email = (local: string) => `${local}.${runId}@example.com`
  return [
    {
      // 5/5 skills (40) + 8 years (30) + 2 projects (10) + degree (7) + 3 = 90
      name: "Asha Verma",
      email: email("asha.verma"),
      filename: `asha-verma-${runId}.pdf`,
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
      filename: `rahul-iyer-${runId}.pdf`,
      category: "Moderate match",
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
      filename: `meera-nair-${runId}.pdf`,
      category: "Weak match",
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
