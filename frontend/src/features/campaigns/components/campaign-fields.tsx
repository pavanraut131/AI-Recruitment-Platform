"use client"

import { AlertCircleIcon } from "lucide-react"
import { useFormContext } from "react-hook-form"

import { FormControl, FormDescription, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"

import { DESCRIPTION_MAX, SKILLS_MAX, type CampaignFormInput, type CampaignFormValues } from "../schema"
import { SkillsInput } from "./skills-input"

/** The fields a campaign is created and edited with. Render inside `<Form>` for a campaign form. */
export function CampaignFields({ disabled }: { disabled?: boolean }) {
  const form = useFormContext<CampaignFormInput, unknown, CampaignFormValues>()
  const description = form.watch("description")

  return (
    <div className="flex flex-col gap-5">
      <FormField
        control={form.control}
        name="title"
        render={({ field }) => (
          <FormItem>
            <FormLabel>Title</FormLabel>
            <FormControl>
              <Input placeholder="Senior backend engineer, Q1" autoComplete="off" disabled={disabled} {...field} />
            </FormControl>
            <FormDescription>Your name for this campaign, shown in the campaign list.</FormDescription>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={form.control}
        name="role"
        render={({ field }) => (
          <FormItem>
            <FormLabel>Role</FormLabel>
            <FormControl>
              <Input placeholder="Backend Engineer" autoComplete="off" disabled={disabled} {...field} />
            </FormControl>
            <FormDescription>The job title candidates are matched against.</FormDescription>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={form.control}
        name="description"
        render={({ field }) => (
          <FormItem>
            <FormLabel>
              Description <span className="font-normal text-muted-foreground">(optional)</span>
            </FormLabel>
            <FormControl>
              <Textarea rows={5} placeholder="What the role involves, the team, must-haves…" disabled={disabled} {...field} />
            </FormControl>
            <FormDescription className="flex justify-between gap-4">
              <span>Used by the AI when it scores resumes.</span>
              <span className="shrink-0 tabular-nums">
                {description.length.toLocaleString("en")} / {DESCRIPTION_MAX.toLocaleString("en")}
              </span>
            </FormDescription>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={form.control}
        name="required_skills"
        render={({ field: { value, onChange, onBlur, ref, name } }) => (
          <FormItem>
            <FormLabel>Required skills</FormLabel>
            <FormControl>
              <SkillsInput
                value={value}
                onChange={onChange}
                onBlur={onBlur}
                disabled={disabled}
                fieldRef={ref}
                inputProps={{
                  name,
                  placeholder: value.length === 0 ? "Type a skill and press Enter" : "Add another…",
                }}
              />
            </FormControl>
            <FormDescription>
              Matched against each resume. Press Enter or comma to add; up to {SKILLS_MAX}.
            </FormDescription>
            <FormMessage />
          </FormItem>
        )}
      />
    </div>
  )
}

/** The form-level error of a failed submit (see `applyServerErrors`). */
export function FormServerError({ message }: { message: string | undefined }) {
  if (!message) return null
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
    >
      <AlertCircleIcon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <p>{message}</p>
    </div>
  )
}
