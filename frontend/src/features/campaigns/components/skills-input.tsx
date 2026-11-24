"use client"

import * as React from "react"
import { XIcon } from "lucide-react"

import { cn } from "@/lib/utils"

import { mergeSkills, SKILL_MAX, SKILLS_MAX } from "../schema"

/**
 * Required skills as removable chips plus a text input. Enter or a comma adds
 * what was typed (a pasted "React, Node, SQL" adds three), Backspace in an
 * empty input removes the last chip. Duplicates are skipped case-insensitively.
 *
 * Accessibility: the text input gets the `id`, `aria-describedby` and
 * `aria-invalid` that FormControl's Slot passes to this component, so the
 * label and messages point at it; each chip's remove button is labelled with
 * the skill, and additions and removals are announced in a polite live region.
 */
export function SkillsInput({
  value,
  onChange,
  onBlur,
  disabled,
  fieldRef,
  inputProps,
  className,
  ...aria
}: {
  value: string[]
  onChange: (skills: string[]) => void
  onBlur?: () => void
  disabled?: boolean
  /** react-hook-form's `field.ref`, so focusing the field (e.g. on a server error) focuses the input. */
  fieldRef?: (node: HTMLInputElement | null) => void
  inputProps?: Omit<React.ComponentProps<"input">, "ref" | "value" | "onChange" | "onKeyDown" | "onBlur">
  className?: string
  id?: string
  "aria-describedby"?: string
  "aria-invalid"?: boolean
}) {
  const [draft, setDraft] = React.useState("")
  const [announcement, setAnnouncement] = React.useState("")
  const inputRef = React.useRef<HTMLInputElement>(null)
  const setInputRef = React.useCallback(
    (node: HTMLInputElement | null) => {
      inputRef.current = node
      fieldRef?.(node)
    },
    [fieldRef],
  )

  function add(text: string) {
    const parts = text.split(",")
    const next = mergeSkills(value, parts)
    if (next.length !== value.length) {
      onChange(next)
      const added = next.slice(value.length)
      setAnnouncement(`Added ${added.join(", ")}`)
    }
    setDraft("")
  }

  function remove(skill: string) {
    onChange(value.filter((item) => item !== skill))
    setAnnouncement(`Removed ${skill}`)
    inputRef.current?.focus()
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === ",") {
      // Enter must not submit the surrounding form while a skill is typed.
      event.preventDefault()
      if (draft.trim()) add(draft)
    } else if (event.key === "Backspace" && draft === "" && value.length > 0) {
      event.preventDefault()
      remove(value[value.length - 1])
    }
  }

  return (
    <div
      className={cn(
        "flex min-h-9 w-full flex-wrap items-center gap-1.5 rounded-lg border border-input px-1.5 py-1 transition-colors focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/20 has-aria-invalid:border-destructive has-aria-invalid:ring-3 has-aria-invalid:ring-destructive/20 dark:bg-input/30",
        disabled && "pointer-events-none opacity-50",
        className,
      )}
    >
      <ul className="contents" aria-label="Required skills">
        {value.map((skill) => (
          <li
            key={skill}
            className="inline-flex h-6 items-center gap-1 rounded-full border border-input pr-0.5 pl-2.5 text-dense font-medium"
          >
            {skill}
            <button
              type="button"
              onClick={() => remove(skill)}
              disabled={disabled}
              aria-label={`Remove ${skill}`}
              className="inline-flex size-5 items-center justify-center rounded-full text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
            >
              <XIcon className="size-3" aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>
      <input
        ref={setInputRef}
        {...inputProps}
        {...aria}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={onKeyDown}
        onBlur={() => {
          if (draft.trim()) add(draft)
          onBlur?.()
        }}
        disabled={disabled || value.length >= SKILLS_MAX}
        maxLength={SKILL_MAX}
        className="h-6 min-w-32 flex-1 bg-transparent px-1 text-sm outline-none placeholder:text-muted-foreground"
      />
      <span className="sr-only" aria-live="polite">
        {announcement}
      </span>
    </div>
  )
}
