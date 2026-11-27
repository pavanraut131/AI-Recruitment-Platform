"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Loader2Icon } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { toast } from "sonner"
import type { z } from "zod"

import { FormAlert } from "@/components/auth/form-alert"
import { clientNameSchema } from "@/components/clients/new-client-dialog"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { isApiProblem } from "@/lib/api/problem"
import { problemNotice, setFieldErrors, type ProblemNotice } from "@/lib/forms"
import { useUpdateClient, type Client } from "@/lib/query/clients"

type NameValues = z.infer<typeof clientNameSchema>

/** Rename a client (`PATCH /clients/{id}`): its campaigns show the new name. A taken name is the API's 409. */
export function RenameClientDialog({
  client,
  open,
  onOpenChange,
}: {
  client: Client
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        {open && <RenameForm client={client} onDone={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  )
}

function RenameForm({ client, onDone }: { client: Client; onDone: () => void }) {
  const update = useUpdateClient()
  const [failure, setFailure] = useState<ProblemNotice | null>(null)
  const form = useForm<NameValues>({ resolver: zodResolver(clientNameSchema), defaultValues: { name: client.name } })

  function submit({ name }: NameValues) {
    setFailure(null)
    if (name === client.name) {
      onDone()
      return
    }
    update.mutate(
      { clientId: client.id, body: { name } },
      {
        onSuccess: (saved) => {
          toast.success("Client renamed", { description: `${client.name} is now ${saved.name}.` })
          onDone()
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
      <form onSubmit={form.handleSubmit(submit)} noValidate aria-label={`Rename ${client.name}`} className="flex flex-col gap-4">
        <DialogHeader>
          <DialogTitle className="text-section font-semibold">Rename {client.name}</DialogTitle>
          <DialogDescription>Its campaigns show the new name everywhere.</DialogDescription>
        </DialogHeader>
        <FormField
          control={form.control}
          name="name"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Name</FormLabel>
              <FormControl>
                <Input autoComplete="organization" disabled={update.isPending} {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        {failure && <FormAlert title={failure.title}>{failure.description}</FormAlert>}
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={onDone} disabled={update.isPending}>
            Cancel
          </Button>
          <Button type="submit" disabled={update.isPending}>
            {update.isPending && <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />}
            {update.isPending ? "Saving…" : "Save name"}
          </Button>
        </DialogFooter>
      </form>
    </Form>
  )
}
