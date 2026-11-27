"use client"

import { format, formatDistanceToNowStrict } from "date-fns"
import { Loader2Icon } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import type { Permissions } from "@/lib/auth/permissions"
import { ROLE_LABELS } from "@/lib/auth/roles"
import { problemSentence } from "@/lib/forms"

import { useResendInvitation, useRevokeInvitation, type Invitation } from "../queries"
import { clientAccessText } from "./member-access"

/**
 * Open invitations (EPIC-02 §9.3), newest first: who, the role and clients,
 * who sent it and when it expires. Resend (a new link and 7 more days; it
 * also renews an expired one) and Revoke (the link stops working), for the
 * roles the user may manage (admins: recruiters' and viewers' invitations).
 */
export function InvitationList({
  invitations,
  permissions,
}: {
  invitations: readonly Invitation[]
  permissions: Permissions
}) {
  return (
    <ul aria-label="Pending invitations" className="border-t">
      {invitations.map((invitation) => (
        <InvitationRow
          key={invitation.id}
          invitation={invitation}
          canManage={permissions.can("team:manage") && permissions.canManageRole(invitation.role)}
        />
      ))}
    </ul>
  )
}

function InvitationRow({ invitation, canManage }: { invitation: Invitation; canManage: boolean }) {
  const resend = useResendInvitation()
  const revoke = useRevokeInvitation()
  const [confirmOpen, setConfirmOpen] = useState(false)
  const expired = invitation.status === "expired"
  const expires = new Date(invitation.expires_at)
  const busy = resend.isPending || revoke.isPending

  const facts = [ROLE_LABELS[invitation.role], clientAccessText(invitation)]
  if (invitation.invited_by) facts.push(`Sent by ${invitation.invited_by.display_name}`)

  function onResend() {
    resend.mutate(invitation.id, {
      onSuccess: () =>
        toast.success(`Invitation sent again to ${invitation.email}`, {
          description: "The earlier link no longer works. The new one works for 7 days.",
        }),
      onError: (error) => {
        toast.error("The invitation wasn't sent", { description: problemSentence(error, { tooMany: "Too many invitations today" }) })
      },
    })
  }

  function onRevoke(event: React.MouseEvent) {
    event.preventDefault()
    revoke.mutate(invitation.id, {
      onSuccess: () => {
        setConfirmOpen(false)
        toast.success(`Invitation for ${invitation.email} revoked`)
      },
      onError: (error) => {
        setConfirmOpen(false)
        toast.error("The invitation wasn't revoked", { description: problemSentence(error) })
      },
    })
  }

  return (
    <li className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b px-1 py-3">
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="flex flex-wrap items-center gap-2">
          <span className="truncate font-medium">{invitation.email}</span>
          {expired && <Badge variant="secondary">Expired</Badge>}
        </span>
        <span className="text-dense text-muted-foreground">
          {facts.join(" · ")} ·{" "}
          <time dateTime={invitation.expires_at} title={format(expires, "PPpp")}>
            {expired
              ? `expired ${formatDistanceToNowStrict(expires, { addSuffix: true })}`
              : `expires ${formatDistanceToNowStrict(expires, { addSuffix: true })}`}
          </time>
        </span>
      </div>
      {canManage && (
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="sm" onClick={onResend} disabled={busy} aria-label={`Resend the invitation to ${invitation.email}`}>
            {resend.isPending && <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />}
            {resend.isPending ? "Sending…" : "Resend"}
          </Button>
          <AlertDialog open={confirmOpen} onOpenChange={(open) => !revoke.isPending && setConfirmOpen(open)}>
            <AlertDialogTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                disabled={busy}
                className="text-destructive hover:text-destructive"
                aria-label={`Revoke the invitation to ${invitation.email}`}
              >
                Revoke
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Revoke the invitation to {invitation.email}?</AlertDialogTitle>
                <AlertDialogDescription>
                  The link in their email stops working. You can invite them again at any time.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel disabled={revoke.isPending}>Keep it</AlertDialogCancel>
                <AlertDialogAction variant="destructive" onClick={onRevoke} disabled={revoke.isPending}>
                  {revoke.isPending && <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />}
                  {revoke.isPending ? "Revoking…" : "Revoke invitation"}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      )}
    </li>
  )
}
