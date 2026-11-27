"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Loader2Icon, UserPlusIcon } from "lucide-react"
import { useState } from "react"
import { useForm, useWatch } from "react-hook-form"
import { toast } from "sonner"
import { z } from "zod"

import { FormAlert } from "@/components/auth/form-alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { isApiProblem } from "@/lib/api/problem"
import { ROLES, type Role } from "@/lib/auth/roles"
import { problemNotice, setFieldErrors, type ProblemNotice } from "@/lib/forms"

import { useInvite } from "../queries"
import { accessIssue, accessRequest, ClientAccessFields, RoleField } from "./access-fields"

/** `InvitationCreate.email` in the v2 document. */
const EMAIL_MAX = 320

const inviteSchema = z
  .object({
    email: z
      .string()
      .trim()
      .min(1, "Enter their email address.")
      .max(EMAIL_MAX, `Keep it under ${EMAIL_MAX} characters.`)
      .refine((value) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value), "Enter a valid email address."),
    role: z.enum(ROLES),
    all_clients: z.boolean(),
    client_ids: z.array(z.string()),
  })
  .superRefine((values, context) => {
    const issue = accessIssue(values)
    if (issue) context.addIssue({ code: "custom", path: ["client_ids"], message: issue })
  })
type InviteValues = z.infer<typeof inviteSchema>

/**
 * "Invite a teammate" (EPIC-02 §9.3, `team:manage`): an email address, a role
 * (admins may give recruiter and viewer, owners any role) and, for recruiters
 * and viewers, all clients or some. `POST /team/invitations` emails a link
 * that works for 7 days. The API's answers are shown in the form: a 422 under
 * its field, a 409 (already a member), 403 or 429 above the buttons.
 */
export function InviteDialog({ roles, agencyName }: { roles: readonly Role[]; agencyName: string }) {
  const [open, setOpen] = useState(false)
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <UserPlusIcon data-icon="inline-start" />
          Invite a teammate
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[calc(100svh-2rem)] overflow-y-auto sm:max-w-lg">
        {open && <InviteForm roles={roles} agencyName={agencyName} onDone={() => setOpen(false)} />}
      </DialogContent>
    </Dialog>
  )
}

function InviteForm({
  roles,
  agencyName,
  onDone,
}: {
  roles: readonly Role[]
  agencyName: string
  onDone: () => void
}) {
  const invite = useInvite()
  const [failure, setFailure] = useState<ProblemNotice | null>(null)
  const form = useForm<InviteValues>({
    resolver: zodResolver(inviteSchema),
    defaultValues: {
      email: "",
      role: roles.includes("recruiter") ? "recruiter" : roles[0],
      all_clients: true,
      client_ids: [],
    },
  })
  const role = useWatch({ control: form.control, name: "role" })
  const busy = invite.isPending

  function submit(values: InviteValues) {
    setFailure(null)
    invite.mutate(
      { email: values.email, role: values.role, ...accessRequest(values) },
      {
        onSuccess: (invitation) => {
          toast.success(`Invitation sent to ${invitation.email}`, {
            description: "The link in it works for 7 days.",
          })
          onDone()
        },
        onError: (error) => {
          const mapped = setFieldErrors(error, form.setError, {
            email: "email",
            role: "role",
            all_clients: "client_ids",
            client_ids: "client_ids",
          })
          if (mapped) return
          if (isApiProblem(error) && error.status === 409) {
            form.setError("email", { type: "server", message: error.detail ?? "This address can't be invited." }, { shouldFocus: true })
            return
          }
          setFailure(problemNotice(error, { tooMany: "Too many invitations today" }))
        },
      },
    )
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(submit)} noValidate aria-label="Invite a teammate" className="flex flex-col gap-5">
        <DialogHeader>
          <DialogTitle className="text-section font-semibold">Invite a teammate</DialogTitle>
          <DialogDescription>
            They get an email with a link to join {agencyName}. It works for 7 days.
          </DialogDescription>
        </DialogHeader>
        <FormField
          control={form.control}
          name="email"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Email</FormLabel>
              <FormControl>
                <Input type="email" autoComplete="off" placeholder="name@company.com" disabled={busy} {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <RoleField roles={roles} disabled={busy} />
        <ClientAccessFields role={role} disabled={busy} />
        {failure && <FormAlert title={failure.title}>{failure.description}</FormAlert>}
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={onDone} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" disabled={busy}>
            {busy && <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />}
            {busy ? "Sending…" : "Send invitation"}
          </Button>
        </DialogFooter>
      </form>
    </Form>
  )
}
