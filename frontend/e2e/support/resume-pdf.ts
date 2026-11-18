/**
 * Minimal text PDFs for the E2E uploads, built in memory: one page, Helvetica,
 * one text line per input line. The backend reads them with pdfplumber, and the
 * fake AI provider (AI_PROVIDER=fake) derives the profile from that text (name,
 * e-mail, vocabulary skills, "N years", degree and "Project" lines; see
 * backend/app/modules/ai/fake.py), so each resume's score is known in advance.
 *
 * Generated rather than committed: the content includes the run's id, so every
 * run uploads files no campaign or Person has seen (uploads are de-duplicated
 * by content hash, People are matched by e-mail).
 */

/** Escapes a line for a PDF literal string; only printable ASCII is expected. */
function pdfString(text: string): string {
  if (!/^[\x20-\x7e]*$/.test(text)) throw new Error(`Only printable ASCII in test PDFs: ${text}`)
  return `(${text.replace(/[\\()]/g, (char) => `\\${char}`)})`
}

export function textPdf(lines: readonly string[]): Buffer {
  const content = [
    "BT",
    "/F1 11 Tf",
    "15 TL",
    "72 740 Td",
    ...lines.map((line, index) => `${index === 0 ? "" : "T* "}${pdfString(line)} Tj`),
    "ET",
  ].join("\n")

  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    `<< /Length ${Buffer.byteLength(content, "latin1")} >>\nstream\n${content}\nendstream`,
  ]

  let pdf = "%PDF-1.4\n"
  const offsets: number[] = []
  objects.forEach((body, index) => {
    offsets.push(Buffer.byteLength(pdf, "latin1"))
    pdf += `${index + 1} 0 obj\n${body}\nendobj\n`
  })
  const xref = Buffer.byteLength(pdf, "latin1")
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`
  pdf += offsets.map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`).join("")
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`
  return Buffer.from(pdf, "latin1")
}
