"use client"

import { formatDistanceToNowStrict } from "date-fns"
import { Loader2Icon, MoreHorizontalIcon } from "lucide-react"
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
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { problemSentence } from "@/lib/forms"
import { ROLE_IN_SENTENCE, ROLE_LABELS, type Role } from "@/lib/auth/roles"

import { useSetMemberStatus, useUpdateMember, type Member } from "../queries"
import { ClientAccessDialog } from "./client-access-dialog"
import { clientAccessText, type MemberControls } from "./member-access"

/**
 * The agency's members (EPIC-02 §9.3): name and email, role, client access,
 * status and last activity. What a row offers comes from `memberControls`
 * (the guard rails): a role select, "Change client access…", "Deactivate…" or
 * "Reactivate"; a row that can't be changed says why under its role.
 */
export function MembersTable({
  members,
  controlsFor,
}: {
  members: readonly Member[]
  controlsFor: (member: Member) => MemberControls
}) {
  return (
    <div className="overflow-x-auto border-t">
      <Table className="min-w-[860px] text-dense">
        <TableCaption className="sr-only">The agency&apos;s members, by name.</TableCaption>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="pl-1">Member</TableHead>
            <TableHead>Role</TableHead>
            <TableHead>Client access</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Last active</TableHead>
            <TableHead className="w-12 pr-1">
              <span className="sr-only">Actions</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {members.map((member) => (
            <MemberRow key={member.id} member={member} controls={controlsFor(member)} />
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function MemberRow({ member, controls }: { member: Member; controls: MemberControls }) {
  const name = member.user.display_name
  const deactivated = member.status === "deactivated"
  return (
    <TableRow className="hover:bg-transparent" data-member={member.user.email}>
      <TableCell className="max-w-72 py-2.5 pl-1 align-top">
        <span className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">{name}</span>
          {controls.isSelf && <Badge variant="secondary">You</Badge>}
        </span>
        <span className="block truncate text-muted-foreground">{member.user.email}</span>
      </TableCell>
      <TableCell className="w-52 py-2.5 align-top whitespace-normal">
        {controls.roleOptions.length > 0 ? (
          <RoleSelect member={member} roles={controls.roleOptions} />
        ) : (
          <span className="text-sm">{ROLE_LABELS[member.role]}</span>
        )}
        {controls.reason && <p className="mt-1 max-w-52 text-xs text-muted-foreground">{controls.reason}</p>}
      </TableCell>
      <TableCell className="max-w-64 py-2.5 align-top whitespace-normal text-ink-2">{clientAccessText(member)}</TableCell>
      <TableCell className="py-2.5 align-top">
        {deactivated ? <Badge variant="secondary">Deactivated</Badge> : <span className="text-ink-2">Active</span>}
      </TableCell>
      <TableCell className="py-2.5 align-top whitespace-nowrap text-muted-foreground">
        <LastActive iso={member.last_active_at} />
      </TableCell>
      <TableCell className="py-1.5 pr-1 text-right align-top">
        <MemberActions member={member} controls={controls} />
      </TableCell>
    </TableRow>
  )
}

function LastActive({ iso }: { iso: string | null }) {
  if (!iso) return <>Never</>
  const date = new Date(iso)
  return (
    <time dateTime={iso} title={date.toLocaleString()}>
      {formatDistanceToNowStrict(date, { addSuffix: true })}
    </time>
  )
}

/** The member's role, changed at once (`PATCH`); the API's refusal (403, 409) is said in a toast. */
function RoleSelect({ member, roles }: { member: Member; roles: readonly Role[] }) {
  const update = useUpdateMember()
  const name = member.user.display_name
  return (
    <span className="flex items-center gap-2">
      <Select
        value={member.role}
        disabled={update.isPending}
        onValueChange={(next) => {
          const role = next as Role
          if (role === member.role) return
          update.mutate(
            { memberId: member.id, body: { role } },
            {
              onSuccess: () => toast.success(`${name} is now ${ROLE_IN_SENTENCE[role]}`),
              onError: (error) => {
                toast.error(`${name}'s role wasn't changed`, { description: problemSentence(error) })
              },
            },
          )
        }}
      >
        <SelectTrigger size="sm" aria-label={`Role of ${name}`} className="w-36">
          <SelectValue />
        </SelectTrigger>
        <SelectContent position="popper" align="start">
          {roles.map((role) => (
            <SelectItem key={role} value={role}>
              {ROLE_LABELS[role]}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {update.isPending && (
        <Loader2Icon className="size-3.5 animate-spin text-muted-foreground motion-reduce:animate-none" aria-label="Saving" />
      )}
    </span>
  )
}

function MemberActions({ member, controls }: { member: Member; controls: MemberControls }) {
  const status = useSetMemberStatus()
  const [accessOpen, setAccessOpen] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const name = member.user.display_name

  if (!controls.canEditClients && !controls.canDeactivate && !controls.canReactivate) return null

  function setActive(active: boolean) {
    status.mutate(
      { memberId: member.id, active },
      {
        onSuccess: () => {
          setConfirmOpen(false)
          toast.success(active ? `${name} is active again` : `${name} is deactivated`, {
            description: active ? "They can sign in to this agency again." : "Their access ended at once.",
          })
        },
        onError: (error) => {
          setConfirmOpen(false)
          toast.error(active ? `${name} wasn't reactivated` : `${name} wasn't deactivated`, { description: problemSentence(error) })
        },
      },
    )
  }

  return (
    <>
      {/* Not modal: its items open a dialog, which takes the focus from there. */}
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${name}`} disabled={status.isPending}>
            {status.isPending ? <Loader2Icon className="animate-spin motion-reduce:animate-none" /> : <MoreHorizontalIcon />}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-52">
          {controls.canEditClients && (
            <DropdownMenuItem onSelect={() => setAccessOpen(true)}>Change client access…</DropdownMenuItem>
          )}
          {controls.canDeactivate && (
            <DropdownMenuItem variant="destructive" onSelect={() => setConfirmOpen(true)}>
              Deactivate…
            </DropdownMenuItem>
          )}
          {controls.canReactivate && <DropdownMenuItem onSelect={() => setActive(true)}>Reactivate</DropdownMenuItem>}
        </DropdownMenuContent>
      </DropdownMenu>

      {controls.canEditClients && <ClientAccessDialog member={member} open={accessOpen} onOpenChange={setAccessOpen} />}

      <AlertDialog open={confirmOpen} onOpenChange={(open) => !status.isPending && setConfirmOpen(open)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Deactivate {name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Their access to this agency ends at once. Their account, and any other agency they work in, stay as they
              are. You can reactivate them later.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={status.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={status.isPending}
              onClick={(event) => {
                event.preventDefault()
                setActive(false)
              }}
            >
              {status.isPending && <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />}
              {status.isPending ? "Deactivating…" : "Deactivate"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
