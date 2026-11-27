"use client"

import { Loader2Icon } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { toast } from "sonner"

import { FormAlert } from "@/components/auth/form-alert"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Form } from "@/components/ui/form"
import { ROLE_IN_SENTENCE } from "@/lib/auth/roles"
import { problemNotice, setFieldErrors, type ProblemNotice } from "@/lib/forms"
import { pickableClients, usePickerClients } from "@/lib/query/clients"

import { useUpdateMember, type Member } from "../queries"
import { accessIssue, accessRequest, ClientAccessFields, type AccessValues } from "./access-fields"

/**
 * A recruiter's or viewer's client access (EPIC-02 §9.3): all clients, or the
 * ones chosen. `PATCH /team/members/{id}` with `all_clients` and
 * `client_ids`; the role stays. Archived clients are not offered, so saving
 * drops any the member still had.
 */
export function ClientAccessDialog({
  member,
  open,
  onOpenChange,
}: {
  member: Member
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100svh-2rem)] overflow-y-auto sm:max-w-lg">
        {open && <ClientAccessForm member={member} onDone={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  )
}

function ClientAccessForm({ member, onDone }: { member: Member; onDone: () => void }) {
  const update = useUpdateMember()
  const [failure, setFailure] = useState<ProblemNotice | null>(null)
  const form = useForm<AccessValues>({
    defaultValues: {
      role: member.role,
      all_clients: member.all_clients,
      client_ids: member.clients.map((client) => client.id),
    },
  })
  const name = member.user.display_name
  const busy = update.isPending
  // Their clients that are no longer offered (archived): the API grants active clients only.
  const clients = usePickerClients()
  const active = new Set(pickableClients(clients.data?.items).map((client) => client.id))
  const dropped = clients.data ? member.clients.filter((client) => !active.has(client.id)) : []

  function submit(values: AccessValues) {
    setFailure(null)
    const kept = { ...values, client_ids: values.client_ids.filter((id) => active.has(id)) }
    const issue = accessIssue(kept)
    if (issue) {
      form.setError("client_ids", { type: "validate", message: issue })
      return
    }
    update.mutate(
      { memberId: member.id, body: accessRequest(kept) },
      {
        onSuccess: () => {
          toast.success(`Client access saved for ${name}`)
          onDone()
        },
        onError: (error) => {
          const mapped = setFieldErrors(error, form.setError, { all_clients: "client_ids", client_ids: "client_ids" })
          if (!mapped) setFailure(problemNotice(error))
        },
      },
    )
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(submit)} noValidate aria-label={`Client access for ${name}`} className="flex flex-col gap-5">
        <DialogHeader>
          <DialogTitle className="text-section font-semibold">Client access for {name}</DialogTitle>
          <DialogDescription>
            {name} is {ROLE_IN_SENTENCE[member.role]}. Choose whose campaigns they see. The change applies from their
            next click.
          </DialogDescription>
        </DialogHeader>
        <ClientAccessFields role={member.role} disabled={busy || clients.isPending} />
        {!member.all_clients && dropped.length > 0 && (
          <FormAlert tone="info">
            {dropped.map((client) => client.name).join(", ")} {dropped.length === 1 ? "is" : "are"} archived, so saving
            removes {dropped.length === 1 ? "it" : "them"} from {name}&apos;s access.
          </FormAlert>
        )}
        {failure && <FormAlert title={failure.title}>{failure.description}</FormAlert>}
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={onDone} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" disabled={busy}>
            {busy && <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />}
            {busy ? "Saving…" : "Save access"}
          </Button>
        </DialogFooter>
      </form>
    </Form>
  )
}
