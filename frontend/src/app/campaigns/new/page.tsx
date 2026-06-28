"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { createCampaign, parseJobDescription } from "@/lib/api";
import { ArrowLeft, Plus, X, Briefcase, Sparkles, ChevronDown, ChevronUp } from "lucide-react";
import Link from "next/link";
import { toast } from "react-toastify";

export default function NewCampaignPage() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [role, setRole] = useState("");
  const [description, setDescription] = useState("");
  const [skills, setSkills] = useState<string[]>([]);
  const [skillInput, setSkillInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [jdText, setJdText] = useState("");
  const [jdParsing, setJdParsing] = useState(false);
  const [showJdParser, setShowJdParser] = useState(false);

  const addSkill = () => {
    const s = skillInput.trim();
    if (s && !skills.includes(s)) setSkills([...skills, s]);
    setSkillInput("");
  };

  const removeSkill = (s: string) => setSkills(skills.filter(x => x !== s));

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addSkill(); }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || !role.trim()) { setError("Title and Role are required."); return; }
    setLoading(true); setError("");
    try {
      const campaign = await createCampaign({ title, role, description, required_skills: skills });
      router.push(`/campaigns/${campaign.id}`);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(msg || "Failed to create campaign.");
      setLoading(false);
    }
  };

  const handleParseJd = async () => {
    if (!jdText.trim()) return;
    setJdParsing(true);
    try {
      const result = await parseJobDescription(jdText);
      if (!role && result.role_title) setRole(result.role_title);
      if (result.skills?.length > 0) {
        setSkills(prev => [...new Set([...prev, ...result.skills])]);
        toast.success(`Extracted ${result.skills.length} skills from job description!`);
      }
      setShowJdParser(false);
    } catch (e) {
      toast.error("Failed to parse job description. Please try again.");
    } finally {
      setJdParsing(false);
    }
  };

  const presets: Record<string, string[]> = {
    "AI Engineer":    ["Python", "FastAPI", "LLMs", "Vector DBs", "LangChain"],
    "Frontend Dev":   ["React", "TypeScript", "Next.js", "CSS", "REST APIs"],
    "Backend Dev":    ["Python", "Node.js", "PostgreSQL", "Redis", "Docker"],
    "DevOps":         ["Kubernetes", "Docker", "CI/CD", "AWS", "Terraform"],
  };

  return (
    <div className="page-wrapper">
      {/* Back link */}
      <Link
        href="/campaigns"
        className="inline-flex items-center gap-1.5 text-[12px] font-medium mb-10 transition-colors"
        style={{ color: "rgba(245,240,232,0.32)", textDecoration: "none" }}
        onMouseEnter={e => (e.currentTarget as HTMLElement).style.color = "rgba(245,240,232,0.65)"}
        onMouseLeave={e => (e.currentTarget as HTMLElement).style.color = "rgba(245,240,232,0.32)"}
      >
        <ArrowLeft size={13} strokeWidth={2} />
        Back to Campaigns
      </Link>

      {/* Page header */}
      <div className="mb-10 slide-up">
        <div className="page-eyebrow mb-3">
          <span style={{ opacity: 0.5 }}>—</span>
          New Campaign
        </div>
        <h1 className="text-[30px] font-bold text-[#f5f0e8] tracking-tight leading-tight">
          Create Hiring Campaign
        </h1>
        <p className="text-[13px] mt-2" style={{ color: "rgba(245,240,232,0.38)" }}>
          Define role requirements to power AI-driven candidate matching
        </p>
      </div>

      <form onSubmit={handleSubmit}>
        <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>

          {/* Campaign Details */}
          <div className="form-section slide-up" style={{ animationDelay: "0.04s" }}>
            <div className="form-section-title">Campaign Details</div>
            <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
              <div>
                <label className="form-label" htmlFor="title">
                  Campaign Title <span style={{ color: "#d97706" }}>*</span>
                </label>
                <input
                  id="title"
                  className="input-field"
                  placeholder="e.g. AI Engineer — Batch 1 (June 2026)"
                  value={title}
                  onChange={e => setTitle(e.target.value)}
                  required
                />
              </div>

              <div>
                <label className="form-label" htmlFor="role">
                  Target Job Role <span style={{ color: "#d97706" }}>*</span>
                </label>
                <input
                  id="role"
                  className="input-field"
                  placeholder="e.g. Senior AI Engineer"
                  value={role}
                  onChange={e => setRole(e.target.value)}
                  required
                />
              </div>

              <div>
                <label className="form-label" htmlFor="description">Job Description</label>
                <textarea
                  id="description"
                  className="input-field"
                  style={{ resize: "none", minHeight: "100px", lineHeight: "1.6" }}
                  placeholder="Describe the role, responsibilities, and key requirements..."
                  value={description}
                  onChange={e => setDescription(e.target.value)}
                />
              </div>
            </div>
          </div>

          {/* JD Auto-Parser */}
          <div className="form-section slide-up" style={{ animationDelay: "0.07s" }}>
            <button
              type="button"
              onClick={() => setShowJdParser(!showJdParser)}
              className="flex items-center justify-between w-full text-left"
            >
              <div className="flex items-center gap-2.5">
                <Sparkles size={14} style={{ color: "#d97706" }} />
                <span className="text-[13.5px] font-semibold text-[#f5f0e8]">
                  Extract from Job Description
                </span>
                <span
                  className="text-[10px] px-2 py-0.5 rounded-full font-semibold"
                  style={{ background: "rgba(217,119,6,0.12)", color: "#d97706", border: "1px solid rgba(217,119,6,0.22)" }}
                >
                  AI
                </span>
              </div>
              {showJdParser
                ? <ChevronUp size={15} style={{ color: "rgba(245,240,232,0.35)" }} />
                : <ChevronDown size={15} style={{ color: "rgba(245,240,232,0.35)" }} />
              }
            </button>

            {showJdParser && (
              <div
                className="mt-4 pt-4 flex flex-col gap-3"
                style={{ borderTop: "1px solid rgba(255,248,235,0.06)" }}
              >
                <p className="text-[12px]" style={{ color: "rgba(245,240,232,0.38)" }}>
                  Paste your full job description and our AI will automatically extract the target role and required technical skills.
                </p>
                <textarea
                  className="input-field"
                  style={{ resize: "vertical", minHeight: "140px", fontSize: "13px" }}
                  placeholder="Paste job description here..."
                  value={jdText}
                  onChange={e => setJdText(e.target.value)}
                />
                <button
                  type="button"
                  onClick={handleParseJd}
                  disabled={jdParsing || !jdText.trim()}
                  className="btn-primary w-fit"
                >
                  <Sparkles size={13} />
                  {jdParsing ? "Extracting..." : "Extract Skills & Role"}
                </button>
              </div>
            )}
          </div>

          {/* Required Skills */}
          <div className="form-section slide-up" style={{ animationDelay: "0.09s" }}>
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: "18px" }}>
              <div>
                <div className="form-section-title" style={{ marginBottom: "3px" }}>Required Skills</div>
                <p className="text-[11.5px]" style={{ color: "rgba(245,240,232,0.28)" }}>
                  AI uses these to score and rank candidates
                </p>
              </div>
              {skills.length > 0 && (
                <span
                  className="text-[10.5px] font-semibold rounded-full"
                  style={{
                    color: "#d97706",
                    background: "rgba(217,119,6,0.1)",
                    border: "1px solid rgba(217,119,6,0.2)",
                    padding: "3px 10px",
                  }}
                >
                  {skills.length} added
                </span>
              )}
            </div>

            {/* Quick presets */}
            <div style={{ marginBottom: "14px" }}>
              <p
                className="text-[10px] font-semibold uppercase mb-2.5"
                style={{ color: "rgba(245,240,232,0.22)", letterSpacing: "0.1em", fontFamily: "var(--font-mono)" }}
              >
                Quick presets
              </p>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
                {Object.keys(presets).map(preset => (
                  <button
                    key={preset}
                    type="button"
                    onClick={() => setSkills([...new Set([...skills, ...presets[preset]])])}
                    className="text-[12px] font-medium transition-all"
                    style={{
                      padding: "6px 13px",
                      borderRadius: "8px",
                      color: "rgba(245,240,232,0.55)",
                      background: "rgba(255,248,235,0.04)",
                      border: "1px solid rgba(255,248,235,0.08)",
                      cursor: "pointer",
                    }}
                    onMouseEnter={e => {
                      (e.currentTarget as HTMLElement).style.background = "rgba(217,119,6,0.08)";
                      (e.currentTarget as HTMLElement).style.color = "#d97706";
                      (e.currentTarget as HTMLElement).style.borderColor = "rgba(217,119,6,0.2)";
                    }}
                    onMouseLeave={e => {
                      (e.currentTarget as HTMLElement).style.background = "rgba(255,248,235,0.04)";
                      (e.currentTarget as HTMLElement).style.color = "rgba(245,240,232,0.55)";
                      (e.currentTarget as HTMLElement).style.borderColor = "rgba(255,248,235,0.08)";
                    }}
                  >
                    + {preset}
                  </button>
                ))}
              </div>
            </div>

            {/* Skill input */}
            <div style={{ display: "flex", gap: "10px", marginBottom: "14px" }}>
              <input
                className="input-field"
                style={{ flex: 1 }}
                placeholder="Type a skill and press Enter..."
                value={skillInput}
                onChange={e => setSkillInput(e.target.value)}
                onKeyDown={handleKeyDown}
              />
              <button
                type="button"
                onClick={addSkill}
                style={{
                  width: "40px",
                  height: "40px",
                  borderRadius: "10px",
                  background: "rgba(217,119,6,0.1)",
                  border: "1px solid rgba(217,119,6,0.22)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  cursor: "pointer",
                  flexShrink: 0,
                  transition: "all 0.15s",
                  color: "#d97706",
                }}
                onMouseEnter={e => {
                  (e.currentTarget as HTMLElement).style.background = "rgba(217,119,6,0.18)";
                }}
                onMouseLeave={e => {
                  (e.currentTarget as HTMLElement).style.background = "rgba(217,119,6,0.1)";
                }}
              >
                <Plus size={15} strokeWidth={2.5} />
              </button>
            </div>

            {/* Skills chips */}
            {skills.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
                {skills.map(skill => (
                  <span
                    key={skill}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "6px",
                      fontSize: "12.5px",
                      padding: "5px 12px",
                      borderRadius: "8px",
                      color: "#fbbf24",
                      background: "rgba(217,119,6,0.08)",
                      border: "1px solid rgba(217,119,6,0.2)",
                      fontWeight: 500,
                    }}
                  >
                    {skill}
                    <button
                      type="button"
                      onClick={() => removeSkill(skill)}
                      style={{
                        display: "flex",
                        color: "rgba(251,191,36,0.45)",
                        cursor: "pointer",
                        background: "none",
                        border: "none",
                        padding: 0,
                        transition: "color 0.15s",
                      }}
                      onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = "#fbbf24"; }}
                      onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = "rgba(251,191,36,0.45)"; }}
                    >
                      <X size={11} strokeWidth={2.5} />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Error */}
          {error && (
            <div
              style={{
                borderRadius: "10px",
                padding: "12px 16px",
                fontSize: "13px",
                color: "#f87171",
                background: "rgba(239,68,68,0.07)",
                border: "1px solid rgba(239,68,68,0.18)",
              }}
            >
              {error}
            </div>
          )}

          {/* Actions */}
          <div
            className="slide-up"
            style={{ display: "flex", gap: "10px", animationDelay: "0.12s", paddingTop: "4px" }}
          >
            <button
              type="submit"
              className="btn-primary"
              style={{ flex: 1, justifyContent: "center", padding: "12px 24px", fontSize: "13.5px" }}
              disabled={loading}
            >
              {loading ? (
                <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <svg className="animate-spin" style={{ width: 15, height: 15 }} viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Creating...
                </span>
              ) : (
                <>
                  <Briefcase size={14} strokeWidth={2} />
                  Create Campaign
                </>
              )}
            </button>
            <Link
              href="/campaigns"
              className="btn-secondary"
              style={{ padding: "12px 24px", fontSize: "13.5px" }}
            >
              Cancel
            </Link>
          </div>

        </div>
      </form>
    </div>
  );
}
