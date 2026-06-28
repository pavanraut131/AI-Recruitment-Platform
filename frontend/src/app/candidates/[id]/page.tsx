"use client";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { getCandidate, updateCandidateCategory, updateCandidateNotes, updateCandidatePipeline, Candidate } from "@/lib/api";
import {
  ArrowLeft, Globe, Mail, Phone, Star,
  Briefcase, GraduationCap, Code2, Lightbulb,
  AlertTriangle, CheckCircle2, ChevronDown, ExternalLink, Edit3
} from "lucide-react";

const catStyle: Record<string, { bg: string; text: string; label: string }> = {
  strong_match:   { bg: "rgba(16,185,129,0.1)",  text: "#10b981", label: "Strong Match" },
  moderate_match: { bg: "rgba(6,182,212,0.1)",   text: "#06b6d4", label: "Moderate Match" },
  weak_match:     { bg: "rgba(245,158,11,0.1)",  text: "#f59e0b", label: "Weak Match" },
  rejected:       { bg: "rgba(239,68,68,0.1)",   text: "#ef4444", label: "Rejected" },
};

const CATEGORIES = ["strong_match", "moderate_match", "weak_match", "rejected"];

const PIPELINES = [
  { value: "screened", label: "Screened" },
  { value: "phone_call", label: "Phone Call" },
  { value: "technical", label: "Technical" },
  { value: "offer", label: "Offer" },
  { value: "hired", label: "Hired" },
  { value: "rejected_manual", label: "Rejected" },
];

const pipelineStyle: Record<string, { bg: string; text: string; border: string; label: string }> = {
  screened:        { bg: "rgba(100,116,139,0.1)",  text: "#94a3b8", border: "rgba(100,116,139,0.25)",  label: "Screened" },
  phone_call:      { bg: "rgba(59,130,246,0.1)",   text: "#60a5fa", border: "rgba(59,130,246,0.25)",   label: "Phone Call" },
  technical:       { bg: "rgba(124,58,237,0.1)",   text: "#a78bfa", border: "rgba(124,58,237,0.25)",   label: "Technical" },
  offer:           { bg: "rgba(245,158,11,0.1)",   text: "#fbbf24", border: "rgba(245,158,11,0.25)",   label: "Offer" },
  hired:           { bg: "rgba(16,185,129,0.1)",   text: "#34d399", border: "rgba(16,185,129,0.25)",   label: "Hired" },
  rejected_manual: { bg: "rgba(239,68,68,0.1)",    text: "#f87171", border: "rgba(239,68,68,0.25)",    label: "Rejected" },
};

export default function CandidateDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [loading, setLoading] = useState(true);
  const [categoryUpdating, setCategoryUpdating] = useState(false);
  const [showCategoryMenu, setShowCategoryMenu] = useState(false);
  const [showPipelineMenu, setShowPipelineMenu] = useState(false);
  const [pipelineUpdating, setPipelineUpdating] = useState(false);

  const [notesText, setNotesText] = useState("");
  const [savingNotes, setSavingNotes] = useState<"idle" | "saving" | "saved">("idle");

  useEffect(() => {
    getCandidate(id).then(c => {
      setCandidate(c);
      setNotesText(c.notes || "");
    }).finally(() => setLoading(false));
  }, [id]);

  useEffect(() => {
    if (!candidate || loading || notesText === (candidate.notes || "")) return;
    
    setSavingNotes("saving");
    const timeoutId = setTimeout(async () => {
      try {
        await updateCandidateNotes(candidate.id, notesText);
        setSavingNotes("saved");
        setCandidate(prev => prev ? { ...prev, notes: notesText } : prev);
        setTimeout(() => setSavingNotes("idle"), 2000);
      } catch(e) {
        setSavingNotes("idle");
      }
    }, 800);

    return () => clearTimeout(timeoutId);
  }, [notesText, candidate, loading]);

  const handleCategoryChange = async (cat: string) => {
    if (!candidate) return;
    setCategoryUpdating(true);
    setShowCategoryMenu(false);
    try {
      const updated = await updateCandidateCategory(candidate.id, cat);
      setCandidate(updated);
    } finally {
      setCategoryUpdating(false);
    }
  };

  const handlePipelineChange = async (stage: string) => {
    if (!candidate) return;
    setPipelineUpdating(true);
    setShowPipelineMenu(false);
    try {
      const updated = await updateCandidatePipeline(candidate.id, stage);
      setCandidate(updated);
    } finally {
      setPipelineUpdating(false);
    }
  };

  if (loading) return (
    <div className="p-8 space-y-4 animate-pulse">
      <div className="h-4 bg-white/5 rounded w-40" />
      <div className="h-10 bg-white/5 rounded w-72" />
      <div className="grid grid-cols-3 gap-4 mt-8">
        {[...Array(3)].map((_, i) => <div key={i} className="h-40 bg-white/5 rounded-2xl" />)}
      </div>
    </div>
  );

  if (!candidate) return <div className="p-8 text-white/40">Candidate not found</div>;

  const cat = catStyle[candidate.category || ""] || null;
  const pStyle = pipelineStyle[candidate.pipeline_stage || ""] || null;

  const scoreColor = candidate.score
    ? candidate.score >= 75 ? "#10b981" : candidate.score >= 50 ? "#06b6d4" : candidate.score >= 25 ? "#f59e0b" : "#ef4444"
    : "#64748b";

  return (
    <div className="page-wrapper">
      {/* Back */}
      <Link href={`/campaigns/${candidate.campaign_id}`}
        className="flex items-center gap-2 text-white/40 hover:text-white/70 text-sm mb-6 transition-colors w-fit slide-up">
        <ArrowLeft size={15} /> Back to Campaign
      </Link>

      {/* Hero header */}
      <div className="glass-card relative z-30 p-8 mb-6 slide-up" style={{ animationDelay: "0.05s" }}>
        <div className="flex items-start justify-between gap-6">
          {/* Avatar + info */}
          <div className="flex items-start gap-5">
            <div className="w-16 h-16 rounded-2xl flex items-center justify-center text-2xl font-bold flex-shrink-0"
              style={{ background: "linear-gradient(135deg, rgba(124,58,237,0.3), rgba(6,182,212,0.2))", border: "1px solid rgba(124,58,237,0.25)" }}>
              {candidate.name?.charAt(0) || "?"}
            </div>
            <div>
              <h1 className="text-2xl font-bold text-white">{candidate.name || "Unknown Candidate"}</h1>
              <div className="flex flex-wrap items-center gap-3 mt-2">
                {candidate.email && (
                  <a href={`mailto:${candidate.email}`} className="flex items-center gap-1.5 text-[13px] text-white/40 hover:text-white/70 transition-colors">
                    <Mail size={13} /> {candidate.email}
                  </a>
                )}
                {candidate.phone && (
                  <span className="flex items-center gap-1.5 text-[13px] text-white/40">
                    <Phone size={13} /> {candidate.phone}
                  </span>
                )}
                {candidate.github_url && (
                  <a href={candidate.github_url} target="_blank" rel="noopener noreferrer"
                    className="flex items-center gap-1.5 text-[13px] text-white/40 hover:text-violet-300 transition-colors">
                    <Globe size={13} /> GitHub <ExternalLink size={10} />
                  </a>
                )}
                {candidate.linkedin_url && (
                  <a href={candidate.linkedin_url} target="_blank" rel="noopener noreferrer"
                    className="flex items-center gap-1.5 text-[13px] text-white/40 hover:text-cyan-300 transition-colors">
                    <Globe size={13} /> LinkedIn <ExternalLink size={10} />
                  </a>
                )}
              </div>
              {candidate.years_of_experience != null && (
                <div className="mt-2 text-[13px] text-white/40">
                  <Briefcase size={12} className="inline mr-1" />
                  {candidate.years_of_experience} years of experience
                </div>
              )}
            </div>
          </div>

          {/* Score + category */}
          <div className="flex flex-col items-end gap-3 flex-shrink-0">
            {/* Score ring */}
            <div className="text-center">
              <div className="text-5xl font-black" style={{ color: scoreColor }}>
                {candidate.score != null ? candidate.score.toFixed(0) : "—"}
              </div>
              <div className="text-[11px] text-white/25 mt-0.5">out of 100</div>
            </div>

            {/* Category badge + dropdown */}
            <div className="relative">
              <button onClick={() => setShowCategoryMenu(!showCategoryMenu)} disabled={categoryUpdating}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-[12px] font-semibold transition-all"
                style={cat ? { background: cat.bg, color: cat.text, border: `1px solid ${cat.text}30` } : { background: "rgba(255,255,255,0.06)", color: "#94a3b8" }}>
                {categoryUpdating ? "Updating…" : cat?.label || "Unclassified"}
                <ChevronDown size={12} />
              </button>
              {showCategoryMenu && (
                <div className="absolute right-0 top-full mt-1 z-20 rounded-xl overflow-hidden shadow-2xl"
                  style={{ background: "#0f1117", border: "1px solid rgba(255,255,255,0.1)", minWidth: "160px" }}>
                  {CATEGORIES.map(c => {
                    const s = catStyle[c];
                    return (
                      <button key={c} onClick={() => handleCategoryChange(c)}
                        className="w-full text-left px-4 py-2.5 text-[12px] font-medium hover:bg-white/5 transition-colors flex items-center gap-2"
                        style={{ color: s.text }}>
                        <span className="w-1.5 h-1.5 rounded-full" style={{ background: s.text }} />
                        {s.label}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Pipeline badge + dropdown */}
            <div className="relative">
              <button onClick={() => setShowPipelineMenu(!showPipelineMenu)} disabled={pipelineUpdating}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-[12px] font-semibold transition-all"
                style={pStyle ? { background: pStyle.bg, color: pStyle.text, border: `1px solid ${pStyle.border}` } : { background: "rgba(255,255,255,0.06)", color: "#94a3b8" }}>
                {pipelineUpdating ? "Updating…" : pStyle?.label || "Unassigned"}
                <ChevronDown size={12} />
              </button>
              {showPipelineMenu && (
                <div className="absolute right-0 top-full mt-1 z-20 rounded-xl overflow-hidden shadow-2xl"
                  style={{ background: "#0f1117", border: "1px solid rgba(255,255,255,0.1)", minWidth: "160px" }}>
                  {PIPELINES.map(p => {
                    const s = pipelineStyle[p.value];
                    return (
                      <button key={p.value} onClick={() => handlePipelineChange(p.value)}
                        className="w-full text-left px-4 py-2.5 text-[12px] font-medium hover:bg-white/5 transition-colors flex items-center gap-2"
                        style={{ color: s.text }}>
                        <span className="w-1.5 h-1.5 rounded-full" style={{ background: s.text }} />
                        {s.label}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* AI Summary */}
        {candidate.summary && (
          <div className="mt-6 pt-6 border-t border-white/5">
            <p className="text-[13px] text-white/50 leading-relaxed italic">
              &ldquo;{candidate.summary}&rdquo;
            </p>
          </div>
        )}
      </div>

      {/* Score Breakdown Card */}
      {candidate.score_breakdown && (
        <div className="glass-card p-6 mb-5 slide-up" style={{ animationDelay: "0.08s" }}>
          <h3 className="text-[11px] font-semibold uppercase tracking-widest mb-5"
            style={{ color: "rgba(245,240,232,0.28)", fontFamily: "var(--font-mono)" }}>
            Score Breakdown — How the AI scored this candidate
          </h3>
          <div className="space-y-3.5">
            {([
              { key: "skills_match",          label: "Skills Match",          max: 40, color: "#10b981" },
              { key: "experience_match",       label: "Experience Match",      max: 30, color: "#06b6d4" },
              { key: "project_relevance",      label: "Project Relevance",     max: 15, color: "#a78bfa" },
              { key: "education_bonus",        label: "Education",             max: 10, color: "#f59e0b" },
              { key: "communication_quality",  label: "Resume Quality",        max: 5,  color: "#64748b" },
            ] as const).map(({ key, label, max, color }) => {
              const val = candidate.score_breakdown![key] ?? 0;
              const pct = Math.min((val / max) * 100, 100);
              return (
                <div key={key} className="flex items-center gap-4">
                  <div className="w-36 flex-shrink-0 text-[12px] font-medium"
                    style={{ color: "rgba(245,240,232,0.55)" }}>
                    {label}
                  </div>
                  <div className="flex-1 h-1.5 rounded-full overflow-hidden"
                    style={{ background: "rgba(255,248,235,0.06)" }}>
                    <div className="h-full rounded-full transition-all duration-700"
                      style={{ width: `${pct}%`, background: color }} />
                  </div>
                  <div className="w-14 text-right text-[12px] font-bold tabular-nums flex-shrink-0"
                    style={{ color, fontFamily: "var(--font-mono)" }}>
                    {val}<span className="text-[10px] font-normal opacity-40">/{max}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 3-column grid */}
      <div className="grid grid-cols-3 gap-5 mb-5">
        {/* Strengths */}
        <div className="glass-card p-5 slide-up" style={{ animationDelay: "0.1s" }}>
          <h3 className="flex items-center gap-2 text-[12px] font-semibold text-white/40 uppercase tracking-wider mb-4">
            <CheckCircle2 size={13} className="text-emerald-400" /> Strengths
          </h3>
          <ul className="space-y-2">
            {candidate.strengths?.length ? candidate.strengths.map((s, i) => (
              <li key={i} className="text-[13px] text-white/70 flex items-start gap-2">
                <span className="w-1 h-1 rounded-full bg-emerald-400 mt-2 flex-shrink-0" />
                {s}
              </li>
            )) : <li className="text-[13px] text-white/25">None identified</li>}
          </ul>
        </div>

        {/* Weaknesses */}
        <div className="glass-card p-5 slide-up" style={{ animationDelay: "0.15s" }}>
          <h3 className="flex items-center gap-2 text-[12px] font-semibold text-white/40 uppercase tracking-wider mb-4">
            <AlertTriangle size={13} className="text-amber-400" /> Areas to Improve
          </h3>
          <ul className="space-y-2">
            {candidate.weaknesses?.length ? candidate.weaknesses.map((s, i) => (
              <li key={i} className="text-[13px] text-white/70 flex items-start gap-2">
                <span className="w-1 h-1 rounded-full bg-amber-400 mt-2 flex-shrink-0" />
                {s}
              </li>
            )) : <li className="text-[13px] text-white/25">None identified</li>}
          </ul>
        </div>

        {/* Missing skills */}
        <div className="glass-card p-5 slide-up" style={{ animationDelay: "0.2s" }}>
          <h3 className="flex items-center gap-2 text-[12px] font-semibold text-white/40 uppercase tracking-wider mb-4">
            <Lightbulb size={13} className="text-red-400" /> Missing Skills
          </h3>
          {candidate.missing_skills?.length ? (
            <div className="flex flex-wrap gap-1.5">
              {candidate.missing_skills.map(s => (
                <span key={s} className="text-[11px] px-2 py-1 rounded-md text-red-300"
                  style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)" }}>
                  {s}
                </span>
              ))}
            </div>
          ) : (
            <p className="text-[13px] text-white/25">No missing skills</p>
          )}
        </div>
      </div>

      {/* Skills */}
      <div className="glass-card p-6 mb-5 slide-up" style={{ animationDelay: "0.25s" }}>
        <h3 className="flex items-center gap-2 text-[13px] font-semibold text-white/50 uppercase tracking-wider mb-4">
          <Code2 size={14} className="text-violet-400" /> Skills
        </h3>
        {candidate.skills?.length ? (
          <div className="flex flex-wrap gap-2">
            {candidate.skills.map(s => (
              <span key={s} className="text-[12px] px-3 py-1.5 rounded-lg text-violet-300"
                style={{ background: "rgba(124,58,237,0.1)", border: "1px solid rgba(124,58,237,0.2)" }}>
                {s}
              </span>
            ))}
          </div>
        ) : <p className="text-[13px] text-white/25">No skills extracted</p>}
      </div>

      {/* Work experience */}
      {candidate.work_experience?.length > 0 && (
        <div className="glass-card p-6 mb-5 slide-up" style={{ animationDelay: "0.3s" }}>
          <h3 className="flex items-center gap-2 text-[13px] font-semibold text-white/50 uppercase tracking-wider mb-4">
            <Briefcase size={14} className="text-cyan-400" /> Work Experience
          </h3>
          <div className="space-y-4">
            {candidate.work_experience.map((w, i) => (
              <div key={i} className="flex gap-3">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                  style={{ background: "rgba(6,182,212,0.1)", border: "1px solid rgba(6,182,212,0.2)" }}>
                  <Briefcase size={13} className="text-cyan-400" />
                </div>
                <div>
                  <div className="text-[14px] font-medium text-white">{w.role}</div>
                  <div className="text-[13px] text-white/50">{w.company}</div>
                  <div className="text-[12px] text-white/30 mt-0.5">{w.duration}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Education */}
      {candidate.education?.length > 0 && (
        <div className="glass-card p-6 mb-5 slide-up" style={{ animationDelay: "0.35s" }}>
          <h3 className="flex items-center gap-2 text-[13px] font-semibold text-white/50 uppercase tracking-wider mb-4">
            <GraduationCap size={14} className="text-violet-400" /> Education
          </h3>
          <div className="space-y-3">
            {candidate.education.map((e, i) => (
              <div key={i} className="flex gap-3">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                  style={{ background: "rgba(124,58,237,0.1)" }}>
                  <GraduationCap size={13} className="text-violet-400" />
                </div>
                <div>
                  <div className="text-[14px] font-medium text-white">{e.degree}</div>
                  <div className="text-[13px] text-white/50">{e.institution} {e.year ? `• ${e.year}` : ""}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Recommendation */}
      {candidate.recommendation && (
        <div className="glass-card p-6 slide-up" style={{ animationDelay: "0.4s" }}>
          <h3 className="flex items-center gap-2 text-[13px] font-semibold text-white/50 uppercase tracking-wider mb-3">
            <Star size={14} className="text-yellow-400" /> AI Recommendation
          </h3>
          <p className="text-[14px] text-white/70 leading-relaxed">{candidate.recommendation}</p>
        </div>
      )}

      {/* Recruiter Notes */}
      <div className="glass-card p-6 mt-5 slide-up" style={{ animationDelay: "0.45s" }}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="flex items-center gap-2 text-[13px] font-semibold text-white/50 uppercase tracking-wider">
            <Edit3 size={14} className="text-blue-400" /> Recruiter Notes
          </h3>
          <span className="text-[11px] font-medium text-white/30 flex items-center gap-1.5">
            {savingNotes === "saving" && (
              <><span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" /> Saving...</>
            )}
            {savingNotes === "saved" && (
              <><CheckCircle2 size={12} className="text-emerald-400" /> Saved</>
            )}
            {savingNotes === "idle" && (
              <span>Auto-saves on type</span>
            )}
          </span>
        </div>
        <textarea
          className="w-full bg-white/5 border border-white/10 rounded-xl p-4 text-[13px] text-white focus:outline-none focus:border-blue-500/50 focus:bg-white/10 transition-colors"
          style={{ minHeight: "120px", resize: "vertical" }}
          placeholder="Add notes about this candidate (e.g. 'Called on June 24. Very interested in the role. Need to schedule technical interview...')"
          value={notesText}
          onChange={e => setNotesText(e.target.value)}
        />
      </div>
    </div>
  );
}
