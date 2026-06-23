"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { createCampaign } from "@/lib/api";
import { ArrowLeft, Plus, X, Briefcase, Sparkles, Zap } from "lucide-react";
import Link from "next/link";

export default function NewCampaignPage() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [role, setRole] = useState("");
  const [description, setDescription] = useState("");
  const [skills, setSkills] = useState<string[]>([]);
  const [skillInput, setSkillInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

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

  const presets: Record<string, string[]> = {
    "AI Engineer":    ["Python", "FastAPI", "LLMs", "Vector DBs", "LangChain"],
    "Frontend Dev":   ["React", "TypeScript", "Next.js", "CSS", "REST APIs"],
    "Backend Dev":    ["Python", "Node.js", "PostgreSQL", "Redis", "Docker"],
    "DevOps":         ["Kubernetes", "Docker", "CI/CD", "AWS", "Terraform"],
  };

  return (
    <div className="page-wrapper">

      {/* Back link */}
      <Link href="/campaigns"
        className="inline-flex items-center gap-2 text-sm mb-10 transition-colors"
        style={{ color: "rgba(255,255,255,0.35)", textDecoration: "none" }}
        onMouseEnter={e => (e.currentTarget.style.color = "rgba(255,255,255,0.7)")}
        onMouseLeave={e => (e.currentTarget.style.color = "rgba(255,255,255,0.35)")}>
        <ArrowLeft size={14} />
        Back to Campaigns
      </Link>

      {/* Page header */}
      <div className="slide-up" style={{ marginBottom: "36px" }}>
        <div className="flex items-center gap-2" style={{ marginBottom: "8px" }}>
          <div style={{
            width: 28, height: 28, borderRadius: 8,
            background: "linear-gradient(135deg, #7c3aed, #06b6d4)",
            display: "flex", alignItems: "center", justifyContent: "center",
            boxShadow: "0 4px 12px rgba(124,58,237,0.35)"
          }}>
            <Zap size={13} className="text-white" />
          </div>
          <span style={{ fontSize: "11px", fontWeight: 700, color: "#a78bfa", textTransform: "uppercase", letterSpacing: "0.1em" }}>
            New Campaign
          </span>
        </div>
        <h1 style={{ fontSize: "28px", fontWeight: 800, color: "#fff", letterSpacing: "-0.02em", lineHeight: 1.2 }}>
          Create Hiring Campaign
        </h1>
        <p style={{ fontSize: "14px", color: "rgba(255,255,255,0.38)", marginTop: "8px" }}>
          Define role requirements to power AI-driven candidate matching
        </p>
      </div>

      <form onSubmit={handleSubmit}>
        <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>

          {/* Campaign Details */}
          <div className="form-section slide-up" style={{ animationDelay: "0.05s" }}>
            <div className="form-section-title">Campaign Details</div>
            <div style={{ display: "flex", flexDirection: "column", gap: "18px" }}>

              <div>
                <label className="form-label" htmlFor="title">Campaign Title <span style={{ color: "#a78bfa" }}>*</span></label>
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
                <label className="form-label" htmlFor="role">Target Job Role <span style={{ color: "#a78bfa" }}>*</span></label>
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
                  style={{ resize: "none", minHeight: "110px", lineHeight: "1.6" }}
                  placeholder="Describe the role, responsibilities, and key requirements..."
                  value={description}
                  onChange={e => setDescription(e.target.value)}
                />
              </div>
            </div>
          </div>

          {/* Required Skills */}
          <div className="form-section slide-up" style={{ animationDelay: "0.1s" }}>
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: "20px" }}>
              <div>
                <div className="form-section-title" style={{ marginBottom: "4px" }}>Required Skills</div>
                <p style={{ fontSize: "12px", color: "rgba(255,255,255,0.28)" }}>
                  AI will use these to score and rank candidates
                </p>
              </div>
              {skills.length > 0 && (
                <span style={{
                  fontSize: "11px", fontWeight: 600, color: "#a78bfa",
                  background: "rgba(124,58,237,0.12)", border: "1px solid rgba(124,58,237,0.22)",
                  borderRadius: "20px", padding: "3px 10px"
                }}>
                  {skills.length} skill{skills.length !== 1 ? "s" : ""} added
                </span>
              )}
            </div>

            {/* Quick presets */}
            <div style={{ marginBottom: "16px" }}>
              <p style={{ fontSize: "11px", color: "rgba(255,255,255,0.25)", marginBottom: "10px", textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 600 }}>
                Quick add presets
              </p>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
                {Object.keys(presets).map(preset => (
                  <button key={preset} type="button"
                    onClick={() => setSkills([...new Set([...skills, ...presets[preset]])])}
                    style={{
                      fontSize: "12px", padding: "6px 14px", borderRadius: "8px",
                      color: "rgba(167,139,250,0.8)",
                      background: "rgba(124,58,237,0.08)",
                      border: "1px solid rgba(124,58,237,0.18)",
                      cursor: "pointer", transition: "all 0.18s",
                      fontWeight: 500,
                    }}
                    onMouseEnter={e => { e.currentTarget.style.background = "rgba(124,58,237,0.16)"; e.currentTarget.style.borderColor = "rgba(124,58,237,0.35)"; }}
                    onMouseLeave={e => { e.currentTarget.style.background = "rgba(124,58,237,0.08)"; e.currentTarget.style.borderColor = "rgba(124,58,237,0.18)"; }}>
                    + {preset}
                  </button>
                ))}
              </div>
            </div>

            {/* Skill input row */}
            <div style={{ display: "flex", gap: "10px", marginBottom: "16px" }}>
              <input
                className="input-field"
                style={{ flex: 1 }}
                placeholder="Type a skill and press Enter..."
                value={skillInput}
                onChange={e => setSkillInput(e.target.value)}
                onKeyDown={handleKeyDown}
              />
              <button type="button" onClick={addSkill}
                style={{
                  width: "42px", height: "42px", borderRadius: "10px",
                  background: "rgba(124,58,237,0.15)", border: "1px solid rgba(124,58,237,0.3)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  cursor: "pointer", flexShrink: 0, transition: "all 0.18s",
                  color: "#a78bfa"
                }}
                onMouseEnter={e => { e.currentTarget.style.background = "rgba(124,58,237,0.28)"; }}
                onMouseLeave={e => { e.currentTarget.style.background = "rgba(124,58,237,0.15)"; }}>
                <Plus size={16} />
              </button>
            </div>

            {/* Skills chips */}
            {skills.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
                {skills.map(skill => (
                  <span key={skill} style={{
                    display: "inline-flex", alignItems: "center", gap: "6px",
                    fontSize: "13px", padding: "6px 12px", borderRadius: "8px",
                    color: "#c4b5fd",
                    background: "rgba(124,58,237,0.12)",
                    border: "1px solid rgba(124,58,237,0.25)",
                    fontWeight: 500,
                  }}>
                    {skill}
                    <button type="button" onClick={() => removeSkill(skill)}
                      style={{ display: "flex", color: "rgba(196,181,253,0.5)", cursor: "pointer", background: "none", border: "none", padding: 0, transition: "color 0.15s" }}
                      onMouseEnter={e => { e.currentTarget.style.color = "#c4b5fd"; }}
                      onMouseLeave={e => { e.currentTarget.style.color = "rgba(196,181,253,0.5)"; }}>
                      <X size={12} />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Error */}
          {error && (
            <div style={{
              borderRadius: "10px", padding: "12px 16px", fontSize: "13px", color: "#fca5a5",
              background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)"
            }}>
              {error}
            </div>
          )}

          {/* Actions */}
          <div className="slide-up" style={{ display: "flex", gap: "12px", animationDelay: "0.15s", paddingTop: "4px" }}>
            <button type="submit" className="btn-primary" style={{ flex: 1, justifyContent: "center", padding: "13px 24px", fontSize: "14px" }} disabled={loading}>
              {loading ? (
                <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <svg className="animate-spin" style={{ width: 16, height: 16 }} viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Creating...
                </span>
              ) : (
                <>
                  <Briefcase size={15} />
                  Create Campaign
                </>
              )}
            </button>
            <Link href="/campaigns" className="btn-secondary" style={{ padding: "13px 28px", fontSize: "14px" }}>
              Cancel
            </Link>
          </div>

        </div>
      </form>
    </div>
  );
}
