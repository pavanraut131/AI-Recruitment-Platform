"use client"

import { format } from "date-fns"
import { BuildingIcon, Loader2Icon, MoreHorizontalIcon, PlusIcon, SearchIcon, SearchXIcon } from "lucide-react"
import { debounce, parseAsInteger, parseAsString, parseAsStringLiteral, useQueryStates } from "nuqs"
import { useState } from "react"
import { toast } from "sonner"

import { RequirePermission } from "@/components/auth/no-access"
import { NewClientDialog } from "@/components/clients/new-client-dialog"
import { EmptyState, ErrorState } from "@/components/data-states"
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
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import type { ClientListParams } from "@/lib/api/query-keys"
import { problemSentence } from "@/lib/forms"
import { useClientList, useUpdateClient, type Client } from "@/lib/query/clients"
import { useDebouncedValue } from "@/lib/use-debounced-value"
import { cn } from "@/lib/utils"

import { Pager } from "../pager"
import { SettingsSection } from "../section"
import { RenameClientDialog } from "./rename-client-dialog"

export const CLIENTS_PAGE_SIZE = 20
const SEARCH_DEBOUNCE_MS = 300

/** Which clients the list shows; active ones by default. */
const STATUS_FILTERS = ["active", "archived", "all"] as const
type StatusFilter = (typeof STATUS_FILTERS)[number]
const STATUS_FILTER_LABELS: Record<StatusFilter, string> = { active: "Active", archived: "Archived", all: "All" }

/** The list's URL state (ADR 0007): `?q=finch&status=archived&page=2`; defaults are left out. */
export const clientSettingsSearchParams = {
  q: parseAsString.withDefault(""),
  status: parseAsStringLiteral(STATUS_FILTERS).withDefault("active"),
  page: parseAsInteger.withDefault(1),
}

/**
 * Settings → Clients (EPIC-02 §9.3, `client:manage`): the agency's clients,
 * searched by name and filtered by status; add one, rename one, archive one
 * (hidden from pickers, its campaigns keep it) or restore it.
 */
export function ClientsSettings() {
  return (
    <RequirePermission
      permission="client:manage"
      what="manage clients"
      who="Owners and admins can."
      back={{ href: "/settings/profile", label: "Go to your profile" }}
    >
      <Clients />
    </RequirePermission>
  )
}

function Clients() {
  const [{ q, status, page }, setParams] = useQueryStates(clientSettingsSearchParams)
  const debouncedQ = useDebouncedValue(q, SEARCH_DEBOUNCE_MS)
  const search = q.trim() === "" ? "" : debouncedQ.trim()
  const params: ClientListParams = {
    ...(status !== "all" && { status: [status] }),
    ...(search && { q: search }),
    page: Math.max(1, page),
    page_size: CLIENTS_PAGE_SIZE,
  }
  const list = useClientList(params)
  const filtered = q.trim() !== "" || status !== "active"

  return (
    <SettingsSection
      id="clients-heading"
      title="Clients"
      description="The companies you recruit for. Every campaign is run for one client. Archived clients are hidden from the pickers; their campaigns keep them."
      action={
        <NewClientDialog
          trigger={
            <Button>
              <PlusIcon data-icon="inline-start" />
              New client
            </Button>
          }
        />
      }
    >
      <div className="flex flex-wrap items-center gap-2">
        <InputGroup className="w-full max-w-xs">
          <InputGroupAddon>
            <SearchIcon aria-hidden="true" />
          </InputGroupAddon>
          <InputGroupInput
            type="search"
            aria-label="Search clients by name"
            placeholder="Client name"
            value={q}
            maxLength={200}
            onChange={(event) =>
              void setParams(
                { q: event.target.value || null, page: null },
                { limitUrlUpdates: event.target.value ? debounce(SEARCH_DEBOUNCE_MS) : undefined },
              )
            }
          />
        </InputGroup>
        <div role="group" aria-label="Filter by status" className="flex items-center gap-1">
          {STATUS_FILTERS.map((value) => {
            const pressed = status === value
            return (
              <Button
                key={value}
                type="button"
                size="sm"
                variant="ghost"
                aria-pressed={pressed}
                onClick={() => void setParams({ status: value === "active" ? null : value, page: null })}
                className={cn(
                  "rounded-full border border-transparent",
                  pressed ? "border-input bg-muted text-foreground" : "text-muted-foreground",
                )}
              >
                {STATUS_FILTER_LABELS[value]}
              </Button>
            )
          })}
        </div>
      </div>

      {list.isPending ? (
        <ClientsSkeleton />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} retrying={list.isFetching} />
      ) : list.data.items.length === 0 ? (
        filtered ? (
          <EmptyState
            icon={SearchXIcon}
            title="No clients match"
            description="Try part of the name, or another status."
            action={
              <Button variant="outline" onClick={() => void setParams({ q: null, status: null, page: null })}>
                Clear filters
              </Button>
            }
          />
        ) : (
          <EmptyState
            icon={BuildingIcon}
            title="No clients yet"
            description="Add the companies you recruit for. Every campaign is run for one of them."
          />
        )
      ) : (
        <>
          <div
            aria-busy={list.isPlaceholderData}
            className={cn("overflow-x-auto border-t", list.isPlaceholderData && "opacity-60")}
          >
            <ClientTable clients={list.data.items} />
          </div>
          <Pager
            label="Client pages"
            page={Math.max(1, page)}
            pageSize={CLIENTS_PAGE_SIZE}
            total={list.data.total}
            shown={list.data.items.length}
            noun="client"
            onPage={(next) => void setParams({ page: next <= 1 ? null : next })}
          />
        </>
      )}
    </SettingsSection>
  )
}

function ClientTable({ clients }: { clients: readonly Client[] }) {
  return (
    <Table className="min-w-[560px] text-dense">
      <TableCaption className="sr-only">Clients, by name.</TableCaption>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead className="pl-1">Client</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Added</TableHead>
          <TableHead className="w-12 pr-1">
            <span className="sr-only">Actions</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {clients.map((client) => (
          <ClientRow key={client.id} client={client} />
        ))}
      </TableBody>
    </Table>
  )
}

function ClientRow({ client }: { client: Client }) {
  const update = useUpdateClient()
  const [renameOpen, setRenameOpen] = useState(false)
  const [archiveOpen, setArchiveOpen] = useState(false)
  const archived = client.status === "archived"

  function setStatus(next: Client["status"]) {
    update.mutate(
      { clientId: client.id, body: { status: next } },
      {
        onSuccess: () => {
          setArchiveOpen(false)
          toast.success(next === "archived" ? `${client.name} archived` : `${client.name} restored`, {
            description:
              next === "archived"
                ? "It's hidden from the pickers. Its campaigns keep it."
                : "It's back in the pickers and can take new campaigns.",
          })
        },
        onError: (error) => {
          setArchiveOpen(false)
          toast.error(next === "archived" ? `${client.name} wasn't archived` : `${client.name} wasn't restored`, { description: problemSentence(error) })
        },
      },
    )
  }

  return (
    <TableRow className="hover:bg-transparent">
      <TableCell className="max-w-96 truncate py-2.5 pl-1 text-sm font-medium">{client.name}</TableCell>
      <TableCell className="py-2.5">
        {archived ? <Badge variant="secondary">Archived</Badge> : <span className="text-ink-2">Active</span>}
      </TableCell>
      <TableCell className="py-2.5 whitespace-nowrap text-muted-foreground">
        <time dateTime={client.created_at}>{format(new Date(client.created_at), "d MMM yyyy")}</time>
      </TableCell>
      <TableCell className="py-1.5 pr-1 text-right">
        <DropdownMenu modal={false}>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${client.name}`} disabled={update.isPending}>
              {update.isPending ? <Loader2Icon className="animate-spin motion-reduce:animate-none" /> : <MoreHorizontalIcon />}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-44">
            <DropdownMenuItem onSelect={() => setRenameOpen(true)}>Rename…</DropdownMenuItem>
            {archived ? (
              <DropdownMenuItem onSelect={() => setStatus("active")}>Restore</DropdownMenuItem>
            ) : (
              <DropdownMenuItem variant="destructive" onSelect={() => setArchiveOpen(true)}>
                Archive…
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
        <RenameClientDialog client={client} open={renameOpen} onOpenChange={setRenameOpen} />
        <AlertDialog open={archiveOpen} onOpenChange={(open) => !update.isPending && setArchiveOpen(open)}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Archive {client.name}?</AlertDialogTitle>
              <AlertDialogDescription>
                It disappears from the client pickers, so no new campaign can be run for it. Its campaigns keep it and
                stay where they are. You can restore it at any time.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={update.isPending}>Cancel</AlertDialogCancel>
              <AlertDialogAction
                variant="destructive"
                disabled={update.isPending}
                onClick={(event) => {
                  event.preventDefault()
                  setStatus("archived")
                }}
              >
                {update.isPending && <Loader2Icon data-icon="inline-start" className="animate-spin motion-reduce:animate-none" />}
                {update.isPending ? "Archiving…" : "Archive client"}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </TableCell>
    </TableRow>
  )
}

function ClientsSkeleton() {
  return (
    <div className="border-t" aria-busy="true" aria-label="Loading clients">
      {Array.from({ length: 4 }, (_, index) => (
        <div key={index} className="flex items-center gap-6 border-b px-1 py-3">
          <Skeleton className="h-4 w-48" />
          <Skeleton className="h-3.5 w-14" />
          <Skeleton className="ml-auto h-3.5 w-20" />
        </div>
      ))}
    </div>
  )
}
