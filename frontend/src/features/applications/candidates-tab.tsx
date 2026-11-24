"use client"

import type { OnChangeFn, RowSelectionState } from "@tanstack/react-table"
import { hashKey } from "@tanstack/react-query"
import { useCallback, useMemo, useState, type ReactNode } from "react"

import { cn } from "@/lib/utils"

import { BulkActionsBar, ExportButton } from "./bulk-actions"
import { CandidatesTable, CandidatesTableSkeleton, rowDomId } from "./candidates-table"
import type { Application, Stage } from "./model"
import { CandidatesPagination } from "./pagination"
import { useApplication, useApplicationsList, useBulkAction, useCampaign, useExportApplications } from "./queries"
import { ReviewPanel } from "./review-panel"
import { EmptyPage, LoadError, NoCandidates, NoMatches } from "./states"
import { CandidatesToolbar } from "./toolbar"
import { hasActiveFilters, toFilterParams, useCandidatesUrlState } from "./url-state"
import { useRowShortcuts } from "./use-row-shortcuts"

const NO_ROWS: Application[] = []
const NO_SELECTION: RowSelectionState = {}

function focusRow(applicationId: string) {
  const row = document.getElementById(rowDomId(applicationId))
  if (!row) return
  // Keep focus in the panel if the user moved it there; otherwise follow with focus.
  if (document.activeElement?.closest("[data-slot='sheet-content']")) row.scrollIntoView?.({ block: "nearest" })
  else row.focus()
}

/**
 * The Candidates tab of a campaign workspace (spec §5.1): the ranked table with
 * URL-driven filters, sort and pagination, bulk actions, export and a preview
 * panel. Server state comes from Query, filters/sort/page/open panel from the
 * URL (nuqs), selection and the active row are local (ADR 0007).
 */
export function CandidatesTab({
  campaignId,
  uploadHref = "?tab=uploads",
}: {
  campaignId: string
  /** Where "Upload resumes" leads (the workspace's Uploads tab). */
  uploadHref?: string
}) {
  const { state, listParams, setFilters, clearFilters, setSort, setPage, setPageSize, setOpen } =
    useCandidatesUrlState()
  const list = useApplicationsList(campaignId, listParams)
  const campaign = useCampaign(campaignId)

  const rows = list.data?.items ?? NO_ROWS
  const total = list.data?.total ?? 0
  const filtersActive = hasActiveFilters(state)
  const requiredSkills = useMemo(
    () => new Set((campaign.data?.required_skills ?? []).map((skill) => skill.trim().toLowerCase())),
    [campaign.data],
  )

  // Selection is per view: changing a filter, the sort or the page starts afresh.
  const viewKey = hashKey([campaignId, listParams])
  const [selection, setSelection] = useState<{ viewKey: string; rows: RowSelectionState }>({
    viewKey,
    rows: NO_SELECTION,
  })
  const rowSelection = selection.viewKey === viewKey ? selection.rows : NO_SELECTION
  const onRowSelectionChange = useCallback<OnChangeFn<RowSelectionState>>(
    (updater) =>
      setSelection((previous) => {
        const base = previous.viewKey === viewKey ? previous.rows : NO_SELECTION
        return { viewKey, rows: typeof updater === "function" ? updater(base) : updater }
      }),
    [viewKey],
  )
  const clearSelection = useCallback(() => setSelection({ viewKey, rows: NO_SELECTION }), [viewKey])
  const selectedIds = useMemo(() => rows.filter((row) => rowSelection[row.id]).map((row) => row.id), [rows, rowSelection])

  // The active row (J/K); while the panel is open it is the open application.
  const [activeId, setActiveId] = useState<string | null>(null)
  const openId = state.open
  const currentId = openId ?? activeId

  const onActiveChange = useCallback(
    (applicationId: string) => {
      setActiveId(applicationId)
      if (openId !== null && openId !== applicationId) void setOpen(applicationId)
    },
    [openId, setOpen],
  )
  const onOpen = useCallback(
    (applicationId: string) => {
      setActiveId(applicationId)
      void setOpen(applicationId)
    },
    [setOpen],
  )
  const onClosePanel = useCallback(() => {
    void setOpen(null)
    if (currentId) document.getElementById(rowDomId(currentId))?.focus()
  }, [currentId, setOpen])

  const rowIds = useMemo(() => rows.map((row) => row.id), [rows])
  useRowShortcuts({
    ids: rowIds,
    activeId: currentId,
    enabled: rows.length > 0,
    onMove: (applicationId) => {
      onActiveChange(applicationId)
      focusRow(applicationId)
    },
  })

  const openRow = openId === null ? undefined : rows.find((row) => row.id === openId)
  // A shared link can open a candidate that is not on this page.
  const openDetail = useApplication(openId, openRow === undefined && !list.isPending)
  // Only this campaign's applications: an `open=` id from another campaign reads as not found.
  const openFromLink = openDetail.data?.campaign_id === campaignId ? openDetail.data : undefined
  const openApplication = openRow ?? openFromLink

  const bulk = useBulkAction(campaignId, { onSuccess: clearSelection })
  const exporter = useExportApplications(campaignId)

  const onMove = (stage: Stage) => bulk.mutate({ action: "stage", ids: selectedIds, stage })
  const onReject = () => bulk.mutate({ action: "reject", ids: selectedIds })
  const onExport = () =>
    exporter.mutate(
      selectedIds.length > 0
        ? { ids: selectedIds, sort: state.sort }
        : { ...toFilterParams(state), sort: state.sort },
    )

  const noCandidatesAtAll = list.data !== undefined && total === 0 && !filtersActive

  let body: ReactNode
  if (list.isPending) {
    body = (
      <div className="overflow-x-auto border-t">
        <CandidatesTableSkeleton />
      </div>
    )
  } else if (list.isError && list.data === undefined) {
    body = <LoadError error={list.error} onRetry={() => void list.refetch()} retrying={list.isFetching} />
  } else if (noCandidatesAtAll) {
    body = <NoCandidates uploadHref={uploadHref} />
  } else if (total === 0) {
    body = <NoMatches onClear={clearFilters} />
  } else if (rows.length === 0) {
    body = <EmptyPage onFirstPage={() => void setPage(1)} />
  } else {
    body = (
      <>
        <div className="overflow-x-auto border-t">
          <CandidatesTable
            rows={rows}
            requiredSkills={requiredSkills}
            sort={state.sort}
            onSortChange={(sort) => void setSort(sort)}
            rowSelection={rowSelection}
            onRowSelectionChange={onRowSelectionChange}
            activeId={currentId}
            onActiveChange={onActiveChange}
            onOpen={onOpen}
            isFetching={list.isPlaceholderData}
          />
        </div>
        <CandidatesPagination
          page={state.page}
          pageSize={state.size}
          shown={rows.length}
          total={total}
          onPageChange={(page) => void setPage(page)}
          onPageSizeChange={(size) => void setPageSize(size)}
        />
      </>
    )
  }

  return (
    // While the review panel is open on a wide screen, the table makes room for it (no rows hidden behind it).
    <section aria-label="Candidates" className={cn("flex flex-col gap-4", openId !== null && "xl:pr-[400px]")}>
      {!noCandidatesAtAll && (
        <div className="flex flex-wrap items-start justify-between gap-2">
          <CandidatesToolbar
            state={state}
            onFiltersChange={(patch) => void setFilters(patch)}
            onClearFilters={() => void clearFilters()}
            hasFilters={filtersActive}
          />
          <ExportButton
            selectedCount={selectedIds.length}
            pending={exporter.isPending}
            disabled={total === 0}
            onExport={onExport}
          />
        </div>
      )}
      {selectedIds.length > 0 && (
        <BulkActionsBar
          count={selectedIds.length}
          pending={bulk.isPending}
          onMove={onMove}
          onReject={onReject}
          onClear={clearSelection}
        />
      )}
      {body}
      <ReviewPanel
        open={openId !== null}
        application={openApplication}
        loading={openApplication === undefined && (list.isPending || openDetail.isPending)}
        error={openDetail.isError ? openDetail.error : undefined}
        onRetry={() => void openDetail.refetch()}
        onClose={onClosePanel}
        requiredSkills={campaign.data?.required_skills}
      />
    </section>
  )
}
