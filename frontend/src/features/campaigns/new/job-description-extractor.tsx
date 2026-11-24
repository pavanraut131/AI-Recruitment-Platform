"use client"

import * as React from "react"
import { AlertCircleIcon, CheckCircle2Icon, Loader2Icon, ScanTextIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { errorMessage } from "@/lib/api/client"

import { useParseJobDescription, type ParsedJobDescription } from "../api"
import { jobDescriptionSchema } from "../schema"

/**
 * "Extract with AI" (spec §4): paste a job description, the API's AI reads it
 * (`POST /campaigns/parse-jd`) and `onExtracted` fills the form. Loading,
 * validation and API errors are shown here, in the panel (no toast).
 */
export function JobDescriptionExtractor({
  onExtracted,
  disabled,
}: {
  onExtracted: (parsed: ParsedJobDescription, jobDescription: string) => string
  disabled?: boolean
}) {
  const id = React.useId()
  const [text, setText] = React.useState("")
  const [validationError, setValidationError] = React.useState<string | null>(null)
  const [summary, setSummary] = React.useState<string | null>(null)
  const parse = useParseJobDescription()

  function extract() {
    const checked = jobDescriptionSchema.safeParse(text)
    if (!checked.success) {
      setValidationError(checked.error.issues[0]?.message ?? "Paste a job description first.")
      return
    }
    setValidationError(null)
    setSummary(null)
    parse.mutate(checked.data, {
      onSuccess: (parsed) => setSummary(onExtracted(parsed, checked.data)),
    })
  }

  const apiError = parse.isError ? errorMessage(parse.error) : null
  const error = validationError ?? (apiError ? [apiError.title, apiError.description].filter(Boolean).join(": ") : null)
  const errorId = `${id}-error`
  const hintId = `${id}-hint`

  return (
    <section
      aria-labelledby={`${id}-heading`}
      aria-busy={parse.isPending}
      className="flex flex-col gap-3 rounded-xl bg-canvas p-5"
    >
      <div className="flex flex-col gap-1">
        <h2 id={`${id}-heading`} className="text-section font-semibold">
          Start from a job description
        </h2>
        <p id={hintId} className="text-ink-2">
          Paste the job ad and we fill in the role and the must-have skills for you to check. You can change
          everything afterwards.
        </p>
      </div>
      <Label htmlFor={`${id}-jd`} className="sr-only">
        Job description
      </Label>
      <Textarea
        id={`${id}-jd`}
        rows={6}
        value={text}
        onChange={(event) => {
          setText(event.target.value)
          if (validationError) setValidationError(null)
        }}
        placeholder="Paste the job ad here"
        disabled={disabled || parse.isPending}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${hintId} ${errorId}` : hintId}
        className="bg-background"
      />
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant="outline" onClick={extract} disabled={disabled || parse.isPending}>
          {parse.isPending ? (
            <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />
          ) : (
            <ScanTextIcon data-icon="inline-start" />
          )}
          {parse.isPending ? "Extracting…" : "Extract with AI"}
        </Button>
        <p aria-live="polite" className="text-sm text-muted-foreground">
          {parse.isPending && "Reading the job description…"}
          {!parse.isPending && summary && (
            <span className="inline-flex items-center gap-1.5">
              <CheckCircle2Icon className="size-4 text-primary" aria-hidden="true" />
              {summary}
            </span>
          )}
        </p>
      </div>
      {error && (
        <p id={errorId} role="alert" className="flex items-start gap-2 text-sm text-destructive">
          <AlertCircleIcon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>{error}</span>
        </p>
      )}
    </section>
  )
}
