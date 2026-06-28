"use client";
// eslint-disable-next-line @typescript-eslint/no-unused-vars
import { Candidate, exportCandidatesCsv, updateCandidatePipeline, bulkUpdatePipeline, bulkDeleteCandidates } from "@/lib/api";
import Link from "next/link";
import { useState } from "react";
import { Search, SlidersHorizontal, Trash2, Download, ArrowUpRight } from "lucide-react";
import clsx from "clsx";
import * as Dialog from "@radix-ui/react-dialog";
import { toast } from "react-toastify";

const CATEGORIES = [
  { value: "", label: "All" },
  { value: "strong_match", label: "Strong" },
  { value: "moderate_match", label: "Moderate" },
  { value: "weak_match", label: "Weak" },
  { value: "rejected", label: "Rejected" },
];

const catStyle: Record<string, { bg: string; text: string; border: string; label: string }> = {
  strong_match:   { bg: "rgba(34,197,94,0.1)",   text: "#4ade80", border: "rgba(34,197,94,0.2)",   label: "Strong Match" },
  moderate_match: { bg: "rgba(99,102,241,0.1)",  text: "#a5b4fc", border: "rgba(99,102,241,0.22)", label: "Moderate Match" },
  weak_match:     { bg: "rgba(245,158,11,0.1)",  text: "#fbbf24", border: "rgba(245,158,11,0.2)",  label: "Weak Match" },
  rejected:       { bg: "rgba(239,68,68,0.1)",   text: "#f87171", border: "rgba(239,68,68,0.2)",   label: "Rejected" },
};

const PIPELINES = [
  { value: "", label: "All Stages" },
  { value: "screened", label: "Screened" },
  { value: "phone_call", label: "Phone Call" },
  { value: "technical", label: "Technical" },
  { value: "offer", label: "Offer" },
  { value: "hired", label: "Hired" },
  { value: "rejected_manual", label: "Rejected" },
];

const pipelineStyle: Record<string, { bg: string; text: string; border: string; label: string }> = {
  screened:        { bg: "rgba(255,248,235,0.06)", text: "rgba(245,240,232,0.55)", border: "rgba(255,248,235,0.1)",  label: "Screened" },
  phone_call:      { bg: "rgba(59,130,246,0.1)",   text: "#60a5fa",               border: "rgba(59,130,246,0.22)",  label: "Phone Call" },
  technical:       { bg: "rgba(99,102,241,0.1)",   text: "#a5b4fc",               border: "rgba(99,102,241,0.22)",  label: "Technical" },
  offer:           { bg: "rgba(217,119,6,0.1)",    text: "#fbbf24",               border: "rgba(217,119,6,0.22)",   label: "Offer" },
  hired:           { bg: "rgba(34,197,94,0.1)",    text: "#4ade80",               border: "rgba(34,197,94,0.22)",   label: "Hired" },
  rejected_manual: { bg: "rgba(239,68,68,0.1)",    text: "#f87171",               border: "rgba(239,68,68,0.22)",   label: "Rejected" },
};

interface Props {
  campaignId: string;
  candidates: Candidate[];
  filters: { category: string; min_score: string; max_score: string; search: string; pipeline_stage?: string };
  onFilterChange: (f: Partial<Props["filters"]>) => void;
  onRefresh: () => void;
}

export default function CandidateTable({ campaignId, candidates, filters, onFilterChange, onRefresh }: Props) {
  const [showFilters, setShowFilters] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkStage, setBulkStage] = useState("");
  const [bulkActing, setBulkActing] = useState(false);

  const toggleSelect = (id: string) =>
    setSelectedIds(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const allSelected = candidates.length > 0 && selectedIds.size === candidates.length;
  const toggleAll = () =>
    setSelectedIds(allSelected ? new Set() : new Set(candidates.map(c => c.id)));

  const handleBulkPipeline = async (stage: string) => {
    if (!stage || selectedIds.size === 0) return;
    setBulkActing(true);
    try {
      const { updated } = await bulkUpdatePipeline(campaignId, Array.from(selectedIds), stage);
      toast.success(`Moved ${updated} candidate${updated !== 1 ? 's' : ''} to ${PIPELINES.find(p => p.value === stage)?.label || stage}`);
      setSelectedIds(new Set());
      setBulkStage("");
      onRefresh();
    } catch { toast.error("Bulk update failed"); }
    finally { setBulkActing(false); }
  };

  const handleBulkDelete = async () => {
    if (selectedIds.size === 0) return;
    if (!confirm(`Delete ${selectedIds.size} selected candidate${selectedIds.size !== 1 ? 's' : ''}? This cannot be undone.`)) return;
    setBulkActing(true);
    try {
      const { deleted } = await bulkDeleteCandidates(campaignId, Array.from(selectedIds));
      toast.success(`Deleted ${deleted} candidate${deleted !== 1 ? 's' : ''}`);
      setSelectedIds(new Set());
      onRefresh();
    } catch { toast.error("Bulk delete failed"); }
    finally { setBulkActing(false); }
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      await exportCandidatesCsv(campaignId, filters);
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* ── Filter bar ─────────────────────────────────── */}
      <div className="flex gap-2 flex-wrap items-center">
        <div className="relative flex-1 min-w-52">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "rgba(245,240,232,0.28)" }} />
          <input
            className="input-field pl-9 text-[13px]"
            placeholder="Search by name or email..."
            value={filters.search}
            onChange={e => onFilterChange({ search: e.target.value })}
          />
        </div>

        <button
          onClick={() => setShowFilters(!showFilters)}
          className="btn-secondary gap-2"
          style={{ padding: "9px 14px" }}
        >
          <SlidersHorizontal size={13} />
          Filters
        </button>

        <button
          onClick={handleExport}
          disabled={exporting}
          className="btn-secondary gap-2"
          style={{ padding: "9px 14px" }}
        >
          <Download size={13} />
          {exporting ? "Exporting..." : "Export CSV"}
        </button>

        {/* Category quick filters */}
        <div className="flex gap-1 ml-auto">
          {CATEGORIES.map(c => (
            <button
              key={c.value}
              onClick={() => onFilterChange({ category: c.value })}
              className="text-[11.5px] px-3 py-2 rounded-lg font-medium transition-all"
              style={
                filters.category === c.value
                  ? { background: "rgba(217,119,6,0.12)", color: "#d97706", border: "1px solid rgba(217,119,6,0.25)" }
                  : { color: "rgba(245,240,232,0.38)", background: "rgba(255,248,235,0.03)", border: "1px solid rgba(255,248,235,0.06)" }
              }
            >
              {c.label}
            </button>
          ))}
        </div>
      </div>

      {/* ── Pipeline stage filters ──────────────────────── */}
      <div className="flex items-center gap-2 flex-wrap">
        <span
          className="text-[10px] uppercase font-semibold flex-shrink-0"
          style={{ color: "rgba(245,240,232,0.2)", letterSpacing: "0.1em", fontFamily: "var(--font-mono)" }}
        >
          Pipeline
        </span>
        <div className="flex gap-1 flex-wrap">
          {PIPELINES.map(p => (
            <button
              key={p.value}
              onClick={() => onFilterChange({ pipeline_stage: p.value })}
              className="text-[11px] px-2.5 py-1.5 rounded-lg font-medium transition-all"
              style={
                (filters.pipeline_stage || "") === p.value
                  ? { background: "rgba(255,248,235,0.08)", color: "#f5f0e8", border: "1px solid rgba(255,248,235,0.14)" }
                  : { color: "rgba(245,240,232,0.35)", background: "rgba(255,248,235,0.03)", border: "1px solid rgba(255,248,235,0.05)" }
              }
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* ── Advanced filters ───────────────────────────── */}
      {showFilters && (
        <div
          className="p-4 rounded-xl flex gap-4 slide-up"
          style={{ background: "rgba(255,248,235,0.02)", border: "1px solid rgba(255,248,235,0.07)" }}
        >
          <div className="flex-1">
            <label className="block text-[11px] mb-1.5" style={{ color: "rgba(245,240,232,0.38)" }}>Min Score</label>
            <input
              type="number" min={0} max={100} className="input-field text-[13px]"
              placeholder="0" value={filters.min_score}
              onChange={e => onFilterChange({ min_score: e.target.value })}
            />
          </div>
          <div className="flex-1">
            <label className="block text-[11px] mb-1.5" style={{ color: "rgba(245,240,232,0.38)" }}>Max Score</label>
            <input
              type="number" min={0} max={100} className="input-field text-[13px]"
              placeholder="100" value={filters.max_score}
              onChange={e => onFilterChange({ max_score: e.target.value })}
            />
          </div>
          <div className="flex items-end">
            <button
              onClick={() => onFilterChange({ min_score: "", max_score: "", search: "", category: "", pipeline_stage: "" })}
              className="btn-secondary"
              style={{ padding: "9px 14px" }}
            >
              Reset
            </button>
          </div>
        </div>
      )}

      {/* ── Floating bulk action bar ─────────────────── */}
      {selectedIds.size > 0 && (
        <div
          className="flex items-center gap-3 px-4 py-3 rounded-xl slide-up"
          style={{ background: "rgba(217,119,6,0.08)", border: "1px solid rgba(217,119,6,0.22)" }}
        >
          <span className="text-[12.5px] font-semibold" style={{ color: "#fbbf24" }}>
            {selectedIds.size} selected
          </span>
          <span style={{ color: "rgba(217,119,6,0.3)" }}>|</span>
          <span className="text-[12px]" style={{ color: "rgba(245,240,232,0.45)" }}>Move to stage:</span>
          <select
            value={bulkStage}
            onChange={e => setBulkStage(e.target.value)}
            className="text-[12px] px-2 py-1.5 rounded-lg font-medium"
            style={{ background: "rgba(255,248,235,0.06)", color: "#f5f0e8", border: "1px solid rgba(255,248,235,0.12)", outline: "none" }}
          >
            <option value="" style={{ background: "#131210", color: "#f5f0e8" }}>Choose…</option>
            {PIPELINES.filter(p => p.value !== "").map(p => (
              <option key={p.value} value={p.value} style={{ background: "#131210", color: "#f5f0e8" }}>{p.label}</option>
            ))}
          </select>
          <button
            onClick={() => handleBulkPipeline(bulkStage)}
            disabled={!bulkStage || bulkActing}
            className="px-3 py-1.5 rounded-lg text-[12px] font-semibold transition-all disabled:opacity-40"
            style={{ background: "rgba(217,119,6,0.15)", color: "#d97706", border: "1px solid rgba(217,119,6,0.28)" }}
          >
            {bulkActing ? "Updating…" : "Apply"}
          </button>
          <div className="ml-auto" />
          <button
            onClick={handleBulkDelete}
            disabled={bulkActing}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-semibold transition-all disabled:opacity-40"
            style={{ background: "rgba(239,68,68,0.1)", color: "#f87171", border: "1px solid rgba(239,68,68,0.22)" }}
          >
            <Trash2 size={12} /> Delete selected
          </button>
          <button
            onClick={() => setSelectedIds(new Set())}
            className="text-[11px] px-2 py-1 rounded"
            style={{ color: "rgba(245,240,232,0.35)" }}
          >
            ✕ Clear
          </button>
        </div>
      )}

      {/* ── Table ─────────────────────────────────────── */}
      {candidates.length === 0 ? (
        <div
          className="p-14 text-center rounded-xl"
          style={{ border: "1px solid rgba(255,248,235,0.06)", background: "rgba(255,248,235,0.015)" }}
        >
          <div className="text-3xl mb-3 opacity-20">↯</div>
          <p className="text-[13px]" style={{ color: "rgba(245,240,232,0.35)" }}>
            No candidates found matching the current filters.
          </p>
        </div>
      ) : (
        <div
          className="rounded-xl overflow-x-auto"
          style={{ border: "1px solid rgba(255,248,235,0.07)", background: "#131210" }}
        >
          <table className="w-full min-w-[900px]">
            <thead>
              <tr style={{ borderBottom: "1px solid rgba(255,248,235,0.06)" }}>
                <th className="pl-4 pr-2 py-3 w-8">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={toggleAll}
                    className="w-3.5 h-3.5 accent-amber-500 cursor-pointer"
                  />
                </th>
                {["Candidate", "Skills", "Experience", "Score", "Category", "Pipeline Stage", ""].map(h => (
                  <th
                    key={h}
                    className="px-5 py-3 text-left text-[10px] font-semibold uppercase tracking-widest"
                    style={{ color: "rgba(245,240,232,0.25)", fontFamily: "var(--font-mono)" }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {candidates.map((c, i) => (
                <CandidateRow key={c.id} candidate={c} index={i} onRefresh={onRefresh}
                  selected={selectedIds.has(c.id)} onToggleSelect={() => toggleSelect(c.id)} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-[11px] text-right" style={{ color: "rgba(245,240,232,0.22)", fontFamily: "var(--font-mono)" }}>
        {candidates.length} candidate{candidates.length !== 1 ? "s" : ""}
      </p>
    </div>
  );
}

function CandidateRow({ candidate: c, index, onRefresh, selected, onToggleSelect }: {
  candidate: Candidate;
  index: number;
  onRefresh: () => void;
  selected: boolean;
  onToggleSelect: () => void;
}) {
  const [isDeleting, setIsDeleting] = useState(false);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [showPipelineMenu, setShowPipelineMenu] = useState(false);
  const [pipelineUpdating, setPipelineUpdating] = useState(false);

  const cat = catStyle[c.category || ""] || null;
  const pStyle = pipelineStyle[c.pipeline_stage || ""] || null;

  const scoreColor = c.score
    ? c.score >= 75 ? "#22c55e" : c.score >= 50 ? "#a5b4fc" : c.score >= 25 ? "#fbbf24" : "#ef4444"
    : "rgba(245,240,232,0.3)";

  return (
    <tr
      className="table-row-hover fade-in"
      style={{
        borderBottom: "1px solid rgba(255,248,235,0.04)",
        animationDelay: `${0.02 * index}s`,
        background: selected ? "rgba(217,119,6,0.04)" : undefined,
      }}
    >
      {/* Checkbox */}
      <td className="pl-4 pr-2 py-3.5">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggleSelect}
          className="w-3.5 h-3.5 accent-amber-500 cursor-pointer"
        />
      </td>
      {/* Name + email */}
      <td className="px-5 py-3.5">
        <div className="font-medium text-[13.5px] text-[#f5f0e8] leading-tight">{c.name || "Unknown"}</div>
        <div className="text-[11.5px] mt-0.5" style={{ color: "rgba(245,240,232,0.3)", fontFamily: "var(--font-mono)" }}>
          {c.email || "—"}
        </div>
      </td>

      {/* Skills */}
      <td className="px-5 py-3.5">
        <div className="flex flex-wrap gap-1 max-w-[200px]">
          {c.skills.slice(0, 3).map(s => (
            <span
              key={s}
              className="text-[10px] px-1.5 py-0.5 rounded font-medium"
              style={{ background: "rgba(255,248,235,0.04)", color: "rgba(245,240,232,0.45)", border: "1px solid rgba(255,248,235,0.07)" }}
            >
              {s}
            </span>
          ))}
          {c.skills.length > 3 && (
            <span className="text-[10px]" style={{ color: "rgba(245,240,232,0.25)" }}>
              +{c.skills.length - 3}
            </span>
          )}
        </div>
      </td>

      {/* Experience */}
      <td className="px-5 py-3.5 text-[12.5px]" style={{ color: "rgba(245,240,232,0.45)", fontFamily: "var(--font-mono)" }}>
        {c.years_of_experience != null ? `${c.years_of_experience}y` : "—"}
      </td>

      {/* Score */}
      <td className="px-5 py-3.5">
        <span className="text-[16px] font-bold tabular-nums tracking-tight" style={{ color: scoreColor, fontFamily: "var(--font-mono)" }}>
          {c.score != null ? c.score.toFixed(0) : "—"}
        </span>
        {c.score != null && <span className="text-[10px] ml-0.5" style={{ color: "rgba(245,240,232,0.2)" }}>/100</span>}
      </td>

      {/* Category */}
      <td className="px-5 py-3.5">
        {cat ? (
          <span
            className="text-[10.5px] px-2.5 py-1 rounded-lg font-semibold whitespace-nowrap"
            style={{ background: cat.bg, color: cat.text, border: `1px solid ${cat.border}` }}
          >
            {cat.label}
          </span>
        ) : <span style={{ color: "rgba(245,240,232,0.2)" }}>—</span>}
      </td>

      {/* Pipeline Stage */}
      <td className="px-5 py-3.5 relative">
        <button
          onClick={() => setShowPipelineMenu(!showPipelineMenu)}
          disabled={pipelineUpdating}
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[10.5px] font-semibold transition-all whitespace-nowrap"
          style={
            pStyle
              ? { background: pStyle.bg, color: pStyle.text, border: `1px solid ${pStyle.border}` }
              : { background: "rgba(255,248,235,0.05)", color: "rgba(245,240,232,0.35)", border: "1px solid rgba(255,248,235,0.08)" }
          }
        >
          {pipelineUpdating ? "Updating..." : pStyle?.label || "Unassigned"}
        </button>
        {showPipelineMenu && (
          <div
            className="absolute left-5 top-full mt-1 z-20 rounded-xl overflow-hidden shadow-2xl"
            style={{ background: "#1a1814", border: "1px solid rgba(255,248,235,0.1)", minWidth: "148px", boxShadow: "0 8px 32px rgba(0,0,0,0.5)" }}
          >
            {PIPELINES.filter(p => p.value !== "").map(p => {
              const s = pipelineStyle[p.value];
              return (
                <button
                  key={p.value}
                  onClick={async () => {
                    setPipelineUpdating(true);
                    setShowPipelineMenu(false);
                    try {
                      await updateCandidatePipeline(c.id, p.value);
                      onRefresh();
                    } catch(e) {}
                    finally { setPipelineUpdating(false); }
                  }}
                  className="w-full text-left px-3.5 py-2.5 text-[11.5px] font-medium flex items-center gap-2 transition-colors"
                  style={{ color: s.text }}
                  onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "rgba(255,248,235,0.04)"}
                  onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "transparent"}
                >
                  <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: s.text }} />
                  {s.label}
                </button>
              );
            })}
          </div>
        )}
      </td>

      {/* Actions */}
      <td className="px-5 py-3.5 whitespace-nowrap">
        <div className="flex items-center gap-2.5">
          <Link
            href={`/candidates/${c.id}`}
            className="flex items-center gap-1 text-[12px] font-medium transition-colors"
            style={{ color: "rgba(217,119,6,0.65)" }}
            onMouseEnter={e => (e.currentTarget as HTMLElement).style.color = "#d97706"}
            onMouseLeave={e => (e.currentTarget as HTMLElement).style.color = "rgba(217,119,6,0.65)"}
          >
            View <ArrowUpRight size={11} strokeWidth={2.5} />
          </Link>
          <Dialog.Root open={isModalOpen} onOpenChange={setIsModalOpen}>
            <Dialog.Trigger asChild>
              <button
                className="transition-colors p-1"
                style={{ color: "rgba(239,68,68,0.4)" }}
                title="Delete Candidate"
                onMouseEnter={e => (e.currentTarget as HTMLElement).style.color = "#ef4444"}
                onMouseLeave={e => (e.currentTarget as HTMLElement).style.color = "rgba(239,68,68,0.4)"}
              >
                <Trash2 size={13} strokeWidth={1.75} />
              </button>
            </Dialog.Trigger>
            <Dialog.Portal>
              <Dialog.Overlay className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50" />
              <Dialog.Content
                className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-full max-w-sm focus:outline-none slide-up"
                style={{
                  background: "#1a1814",
                  border: "1px solid rgba(255,248,235,0.1)",
                  borderRadius: "18px",
                  padding: "28px",
                  boxShadow: "0 24px 64px rgba(0,0,0,0.6)",
                }}
              >
                <Dialog.Title
                  className="text-[17px] font-bold text-[#f5f0e8] mb-2 tracking-tight"
                >
                  Delete Candidate
                </Dialog.Title>
                <Dialog.Description
                  className="text-[13px] mb-7 leading-relaxed"
                  style={{ color: "rgba(245,240,232,0.45)" }}
                >
                  Are you sure you want to delete{" "}
                  <strong className="text-[#f5f0e8] font-semibold">{c.name || "this candidate"}</strong>?
                  This action cannot be undone.
                </Dialog.Description>
                <div className="flex gap-2.5 justify-end">
                  <Dialog.Close asChild>
                    <button className="btn-secondary" disabled={isDeleting} style={{ padding: "9px 18px" }}>
                      Cancel
                    </button>
                  </Dialog.Close>
                  <button
                    className="flex items-center justify-center gap-2 px-5 py-2 text-[13px] font-semibold rounded-xl transition-all"
                    style={{ background: "rgba(239,68,68,0.12)", color: "#f87171", border: "1px solid rgba(239,68,68,0.25)" }}
                    disabled={isDeleting}
                    onClick={async () => {
                      setIsDeleting(true);
                      try {
                        const { deleteCandidate } = await import('@/lib/api');
                        await deleteCandidate(c.id);
                        toast.success("Candidate deleted");
                        onRefresh();
                      } catch (err) {
                        console.error(err);
                        toast.error("Failed to delete candidate");
                      } finally {
                        setIsDeleting(false);
                        setIsModalOpen(false);
                      }
                    }}
                  >
                    {isDeleting ? "Deleting..." : "Delete"}
                  </button>
                </div>
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
        </div>
      </td>
    </tr>
  );
}
