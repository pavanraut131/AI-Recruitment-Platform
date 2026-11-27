"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Loader2Icon } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
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
import { problemNotice, setFieldErrors, type ProblemNotice } from "@/lib/forms"
import { useCreateClient, type Client } from "@/lib/query/clients"

/** `ClientCreate.name` in the v2 document. */
export const CLIENT_NAME_MAX = 200

export const clientNameSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Enter the client's name.")
    .max(CLIENT_NAME_MAX, `Keep the name under ${CLIENT_NAME_MAX} characters.`),
})
type ClientNameValues = z.infer<typeof clientNameSchema>

/**
 * "New client" (`client:manage`, EPIC-02 §9.3): a name, then `POST /clients`.
 * The new-campaign form opens it inline and selects the client it creates;
 * Settings → Clients opens it from its header. A name the agency already
 * uses (archived clients included) is the API's 409, shown under the field.
 *
 * Its form is rendered in a portal but inside the caller's React tree, so its
 * submit event is stopped from reaching a form around the trigger.
 */
export function NewClientDialog({
  trigger,
  onCreated,
}: {
  trigger: React.ReactNode
  onCreated?: (client: Client) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="sm:max-w-md">
        {/* Remounted on each opening: a fresh, empty form. */}
        {open && (
          <NewClientForm
            onCreated={(client) => {
              setOpen(false)
              onCreated?.(client)
            }}
            onCancel={() => setOpen(false)}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function NewClientForm({ onCreated, onCancel }: { onCreated: (client: Client) => void; onCancel: () => void }) {
  const create = useCreateClient()
  const [failure, setFailure] = useState<ProblemNotice | null>(null)
  const form = useForm<ClientNameValues>({ resolver: zodResolver(clientNameSchema), defaultValues: { name: "" } })

  function submit(values: ClientNameValues) {
    setFailure(null)
    create.mutate(
      { name: values.name },
      {
        onSuccess: (client) => {
          toast.success("Client added", { description: client.name })
          onCreated(client)
        },
        onError: (error) => {
          if (isApiProblem(error) && error.status === 409) {
            form.setError("name", { type: "server", message: error.detail ?? "This name is taken." }, { shouldFocus: true })
            return
          }
          if (!setFieldErrors(error, form.setError, { name: "name" })) setFailure(problemNotice(error))
        },
      },
    )
  }

  return (
    <Form {...form}>
      <form
        aria-label="New client"
        noValidate
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          // Not the form around the dialog's trigger (React events bubble through portals).
          event.stopPropagation()
          void form.handleSubmit(submit)(event)
        }}
      >
        <DialogHeader>
          <DialogTitle className="text-section font-semibold">New client</DialogTitle>
          <DialogDescription>The company you recruit for. Every campaign is run for one client.</DialogDescription>
        </DialogHeader>
        <FormField
          control={form.control}
          name="name"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Name</FormLabel>
              <FormControl>
                <Input autoComplete="organization" placeholder="Finch Pay" disabled={create.isPending} {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        {failure && <FormAlert title={failure.title}>{failure.description}</FormAlert>}
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={onCancel} disabled={create.isPending}>
            Cancel
          </Button>
          <Button type="submit" disabled={create.isPending}>
            {create.isPending && <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />}
            {create.isPending ? "Adding…" : "Add client"}
          </Button>
        </DialogFooter>
      </form>
    </Form>
  )
}
