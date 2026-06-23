"use client";
// eslint-disable-next-line @typescript-eslint/no-unused-vars
import { Candidate } from "@/lib/api";
import Link from "next/link";
import { useState } from "react";
import { Search, SlidersHorizontal, ChevronRight, Trash2 } from "lucide-react";
import clsx from "clsx";
import * as Dialog from "@radix-ui/react-dialog";
import { toast } from "react-toastify";

const CATEGORIES = [
  { value: "", label: "All" },
  { value: "strong_match", label: "Strong Match" },
  { value: "moderate_match", label: "Moderate Match" },
  { value: "weak_match", label: "Weak Match" },
  { value: "rejected", label: "Rejected" },
];

const catStyle: Record<string, { bg: string; text: string; border: string; label: string }> = {
  strong_match:   { bg: "rgba(16,185,129,0.1)",  text: "#10b981", border: "rgba(16,185,129,0.25)",  label: "Strong Match" },
  moderate_match: { bg: "rgba(6,182,212,0.1)",   text: "#06b6d4", border: "rgba(6,182,212,0.25)",   label: "Moderate Match" },
  weak_match:     { bg: "rgba(245,158,11,0.1)",  text: "#f59e0b", border: "rgba(245,158,11,0.25)",  label: "Weak Match" },
  rejected:       { bg: "rgba(239,68,68,0.1)",   text: "#ef4444", border: "rgba(239,68,68,0.25)",   label: "Rejected" },
};

interface Props {
  candidates: Candidate[];
  filters: { category: string; min_score: string; max_score: string; search: string };
  onFilterChange: (f: Partial<Props["filters"]>) => void;
  onRefresh: () => void;
}

export default function CandidateTable({ candidates, filters, onFilterChange, onRefresh }: Props) {
  const [showFilters, setShowFilters] = useState(false);

  return (
    <div className="space-y-4">
      {/* Filter bar */}
      <div className="flex gap-2 flex-wrap">
        <div className="relative flex-1 min-w-52">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-white/30" />
          <input
            className="input-field pl-9"
            placeholder="Search by name or email..."
            value={filters.search}
            onChange={e => onFilterChange({ search: e.target.value })}
          />
        </div>

        <button onClick={() => setShowFilters(!showFilters)} className="btn-secondary px-4 gap-2">
          <SlidersHorizontal size={14} />
          Filters
        </button>

        {/* Category quick filters */}
        <div className="flex gap-1">
          {CATEGORIES.map(c => (
            <button key={c.value} onClick={() => onFilterChange({ category: c.value })}
              className={clsx(
                "text-[12px] px-3 py-2 rounded-lg font-medium transition-all",
                filters.category === c.value
                  ? "bg-violet-600/20 text-violet-300 border border-violet-500/30"
                  : "text-white/40 hover:text-white/70 bg-white/4 border border-white/6"
              )}>
              {c.label}
            </button>
          ))}
        </div>
      </div>

      {/* Advanced filters */}
      {showFilters && (
        <div className="glass-card p-4 flex gap-4 slide-up">
          <div className="flex-1">
            <label className="block text-[11px] text-white/40 mb-1.5">Min Score</label>
            <input type="number" min={0} max={100} className="input-field"
              placeholder="0" value={filters.min_score}
              onChange={e => onFilterChange({ min_score: e.target.value })} />
          </div>
          <div className="flex-1">
            <label className="block text-[11px] text-white/40 mb-1.5">Max Score</label>
            <input type="number" min={0} max={100} className="input-field"
              placeholder="100" value={filters.max_score}
              onChange={e => onFilterChange({ max_score: e.target.value })} />
          </div>
          <div className="flex items-end">
            <button onClick={() => onFilterChange({ min_score: "", max_score: "", search: "", category: "" })}
              className="btn-secondary py-2.5">Reset</button>
          </div>
        </div>
      )}

      {/* Table */}
      {candidates.length === 0 ? (
        <div className="glass-card p-12 text-center">
          <div className="text-white/20 text-4xl mb-3">👤</div>
          <p className="text-white/40 text-[13px]">No candidates found matching filters.</p>
        </div>
      ) : (
        <div className="glass-card overflow-hidden">
          <table className="w-full">
            <thead>
              <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                {["Candidate", "Skills", "Experience", "Score", "Category", ""].map(h => (
                  <th key={h} className="px-5 py-3 text-left text-[11px] font-semibold text-white/30 uppercase tracking-wider">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {candidates.map((c, i) => (
                <CandidateRow key={c.id} candidate={c} index={i} onRefresh={onRefresh} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-[12px] text-white/25 text-right">
        {candidates.length} candidate{candidates.length !== 1 ? "s" : ""}
      </p>
    </div>
  );
}

function CandidateRow({ candidate: c, index, onRefresh }: { candidate: Candidate; index: number; onRefresh: () => void }) {
  const [isDeleting, setIsDeleting] = useState(false);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const cat = catStyle[c.category || ""] || null;
  const scoreColor = c.score
    ? c.score >= 75 ? "#10b981" : c.score >= 50 ? "#06b6d4" : c.score >= 25 ? "#f59e0b" : "#ef4444"
    : "#64748b";

  return (
    <tr className="hover:bg-white/2 transition-colors fade-in" style={{ animationDelay: `${0.02 * index}s` }}>
      {/* Name + email */}
      <td className="px-5 py-4">
        <div className="font-medium text-[14px] text-white">{c.name || "Unknown"}</div>
        <div className="text-[12px] text-white/35 mt-0.5">{c.email || "—"}</div>
      </td>

      {/* Skills */}
      <td className="px-5 py-4">
        <div className="flex flex-wrap gap-1 max-w-xs">
          {c.skills.slice(0, 3).map(s => (
            <span key={s} className="text-[10px] px-1.5 py-0.5 rounded text-white/40"
              style={{ background: "rgba(255,255,255,0.05)" }}>
              {s}
            </span>
          ))}
          {c.skills.length > 3 && (
            <span className="text-[10px] text-white/25">+{c.skills.length - 3}</span>
          )}
        </div>
      </td>

      {/* Experience */}
      <td className="px-5 py-4 text-[13px] text-white/50">
        {c.years_of_experience != null ? `${c.years_of_experience}y` : "—"}
      </td>

      {/* Score */}
      <td className="px-5 py-4">
        <span className="text-lg font-bold" style={{ color: scoreColor }}>
          {c.score != null ? c.score.toFixed(0) : "—"}
        </span>
        {c.score != null && <span className="text-[11px] text-white/25">/100</span>}
      </td>

      {/* Category */}
      <td className="px-5 py-4">
        {cat ? (
          <span className="tag text-[10px] px-2.5 py-1 rounded-lg font-semibold"
            style={{ background: cat.bg, color: cat.text, border: `1px solid ${cat.border}` }}>
            {cat.label}
          </span>
        ) : <span className="text-white/25">—</span>}
      </td>

      {/* Actions */}
      <td className="px-5 py-4">
        <div className="flex items-center gap-3">
          <Link href={`/candidates/${c.id}`}
            className="flex items-center gap-1 text-[12px] text-violet-400 hover:text-violet-300 transition-colors">
            View <ChevronRight size={13} />
          </Link>
          <Dialog.Root open={isModalOpen} onOpenChange={setIsModalOpen}>
            <Dialog.Trigger asChild>
              <button 
                className="text-red-400/50 hover:text-red-400 transition-colors p-1"
                title="Delete Candidate"
              >
                <Trash2 size={14} />
              </button>
            </Dialog.Trigger>
            <Dialog.Portal>
              <Dialog.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50" />
              <Dialog.Content className="fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-50 glass-card p-6 w-full max-w-sm rounded-2xl shadow-xl focus:outline-none">
                <Dialog.Title className="text-lg font-bold text-white mb-2 tracking-tight">Delete Candidate</Dialog.Title>
                <Dialog.Description className="text-[14px] text-white/50 mb-6 leading-relaxed">
                  Are you sure you want to delete <strong className="text-white">{c.name || 'this candidate'}</strong>? This action cannot be undone and will remove their extracted data.
                </Dialog.Description>
                <div className="flex gap-3 justify-end mt-2">
                  <Dialog.Close asChild>
                    <button className="btn-secondary px-5 py-2 text-[13px] font-medium" disabled={isDeleting}>Cancel</button>
                  </Dialog.Close>
                  <button 
                    className="flex items-center justify-center gap-2 px-5 py-2 text-[13px] font-semibold rounded-lg transition-all"
                    style={{ background: "rgba(239,68,68,0.15)", color: "#f87171", border: "1px solid rgba(239,68,68,0.3)" }}
                    disabled={isDeleting}
                    onClick={async () => {
                      setIsDeleting(true);
                      try {
                        const { deleteCandidate } = await import('@/lib/api');
                        await deleteCandidate(c.id);
                        toast.success("Candidate deleted successfully");
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
