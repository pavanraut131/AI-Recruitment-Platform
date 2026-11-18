"use client"

import { FileUpIcon } from "lucide-react"
import { useCallback } from "react"
import { useDropzone, type FileRejection } from "react-dropzone"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

import { MAX_FILE_SIZE_MB, MAX_FILES_PER_BATCH, PDF_ACCEPT } from "./limits"
import { checkFile } from "./validate"

/**
 * The drop target for PDF resumes (spec §5.3). Files can be dropped anywhere
 * on it, or picked with the "Choose PDFs" button (click, Enter or Space). The
 * button is the only tab stop: the zone itself is not focusable, so there is
 * no second, unlabelled control. Clicking the zone opens the picker too (the
 * button's click bubbles to it).
 *
 * Every file goes through {@link checkFile}, the server's own type and size
 * checks; `onFiles` gets the files that pass and the reasons for the others.
 */
export function ResumeDropzone({
  onFiles,
  disabled = false,
}: {
  onFiles: (accepted: File[], rejected: { file: File; reason: string }[]) => void
  disabled?: boolean
}) {
  const onDrop = useCallback(
    (accepted: File[], rejections: FileRejection[]) => {
      onFiles(
        accepted,
        rejections.map(({ file, errors }) => ({ file, reason: checkFile(file) ?? errors[0]?.message ?? "Not accepted" })),
      )
    },
    [onFiles],
  )
  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    disabled,
    multiple: true,
    // No `accept` / `maxSize` here: they would reject with react-dropzone's
    // wording. `validator` gives the server's reasons instead.
    validator: (file) => {
      const reason = checkFile(file)
      return reason ? { code: "hireflow-check", message: reason } : null
    },
    noKeyboard: true,
  })

  return (
    <div
      {...getRootProps({
        role: "group",
        "aria-label": "Upload resumes",
        className: cn(
          "flex flex-col items-center gap-3 rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors",
          isDragActive ? "border-primary bg-primary/5" : "border-border hover:bg-muted/40",
          disabled ? "opacity-60" : "cursor-pointer",
        ),
      })}
      data-testid="resume-dropzone"
    >
      {/* The picker filters to PDFs; the checks above apply to dropped files too. */}
      <input {...getInputProps({ accept: PDF_ACCEPT })} aria-label="PDF resumes" />
      <div className="flex size-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
        <FileUpIcon className="size-5" aria-hidden="true" />
      </div>
      <div className="flex flex-col gap-1">
        <p className="font-medium">{isDragActive ? "Drop to upload" : "Drop PDF resumes here"}</p>
        <p className="text-sm text-muted-foreground" id="resume-dropzone-limits">
          PDF only, up to {MAX_FILE_SIZE_MB} MB each. Larger drops go up in batches of {MAX_FILES_PER_BATCH}.
        </p>
      </div>
      <Button type="button" variant="outline" disabled={disabled} aria-describedby="resume-dropzone-limits">
        Choose PDFs
      </Button>
    </div>
  )
}
