"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { getCampaigns, deleteCampaign, Campaign } from "@/lib/api";
import {
  Plus, Briefcase, ArrowUpRight, Trash2, MoreHorizontal,
  Search,
} from "lucide-react";
import clsx from "clsx";

export default function CampaignsPage() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [menuOpen, setMenuOpen] = useState<string | null>(null);

  useEffect(() => {
    getCampaigns().then(setCampaigns).finally(() => setLoading(false));
  }, []);

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this campaign and all its candidates?")) return;
    await deleteCampaign(id);
    setCampaigns(c => c.filter(x => x.id !== id));
    setMenuOpen(null);
  };

  const filtered = campaigns.filter(c =>
    c.title.toLowerCase().includes(search.toLowerCase()) ||
    c.role.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="page-wrapper">
      {/* ── Header ─────────────────────────────────────── */}
      <div className="flex items-end justify-between mb-10 slide-up">
        <div>
          <div className="page-eyebrow mb-3">
            <span style={{ opacity: 0.5 }}>—</span>
            Recruitment
          </div>
          <h1 className="text-[32px] font-bold tracking-tight text-[#f5f0e8] leading-none">
            Campaigns
          </h1>
          <p className="text-[13px] mt-2" style={{ color: "rgba(245,240,232,0.38)" }}>
            {campaigns.length} active hiring campaign{campaigns.length !== 1 ? "s" : ""}
          </p>
        </div>
        <Link href="/campaigns/new" className="btn-primary">
          <Plus size={14} strokeWidth={2.5} />
          New Campaign
        </Link>
      </div>

      {/* ── Search ──────────────────────────────────────── */}
      <div className="relative mb-8 max-w-sm slide-up" style={{ animationDelay: "0.04s" }}>
        <Search size={13} className="absolute left-3.5 top-1/2 -translate-y-1/2" style={{ color: "rgba(245,240,232,0.28)" }} />
        <input
          className="input-field pl-9 text-[13px]"
          placeholder="Search campaigns..."
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
      </div>

      {/* ── Campaign list ───────────────────────────────── */}
      {loading ? (
        <div className="space-y-2.5">
          {[...Array(4)].map((_, i) => (
            <div
              key={i}
              className="h-[72px] rounded-xl animate-pulse"
              style={{ background: "rgba(255,248,235,0.025)", border: "1px solid rgba(255,248,235,0.05)" }}
            />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div
          className="rounded-2xl p-20 text-center"
          style={{ border: "1px dashed rgba(255,248,235,0.07)", background: "rgba(255,248,235,0.01)" }}
        >
          <div
            className="w-14 h-14 rounded-2xl mx-auto mb-6 inline-flex items-center justify-center"
            style={{ background: "rgba(217,119,6,0.08)", border: "1px solid rgba(217,119,6,0.16)" }}
          >
            <Briefcase size={24} style={{ color: "#d97706" }} />
          </div>
          <h3 className="text-[18px] font-bold text-[#f5f0e8] mb-2.5 tracking-tight">No campaigns found</h3>
          <p className="text-[13px] mb-8 max-w-xs mx-auto leading-relaxed" style={{ color: "rgba(245,240,232,0.38)" }}>
            {search
              ? `No campaigns match "${search}".`
              : "Create your first hiring campaign to start analyzing resumes."}
          </p>
          {!search && (
            <Link href="/campaigns/new" className="btn-primary">
              <Plus size={15} />
              Create Campaign
            </Link>
          )}
        </div>
      ) : (
        <div className="space-y-2 slide-up" style={{ animationDelay: "0.06s" }}>
          {filtered.map((c, i) => (
            <div
              key={c.id}
              className="group flex items-center gap-4 px-5 py-4 rounded-xl transition-all duration-150"
              style={{
                background: "#131210",
                border: "1px solid rgba(255,248,235,0.06)",
                animationDelay: `${0.03 * i}s`,
              }}
              onMouseEnter={e => {
                (e.currentTarget as HTMLElement).style.borderColor = "rgba(255,248,235,0.1)";
              }}
              onMouseLeave={e => {
                (e.currentTarget as HTMLElement).style.borderColor = "rgba(255,248,235,0.06)";
              }}
            >
              {/* Icon */}
              <div
                className="w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0"
                style={{ background: "rgba(217,119,6,0.08)", border: "1px solid rgba(217,119,6,0.15)" }}
              >
                <Briefcase size={16} style={{ color: "#d97706" }} strokeWidth={1.75} />
              </div>

              {/* Info */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span
                    className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                    style={{ background: c.status === "active" ? "#22c55e" : "rgba(245,240,232,0.2)" }}
                  />
                  <Link
                    href={`/campaigns/${c.id}`}
                    className="font-semibold text-[14px] text-[#f5f0e8] truncate transition-colors duration-150 group-hover:text-[#fbbf24]"
                  >
                    {c.title}
                  </Link>
                </div>
                <div className="flex items-center gap-2.5 mt-0.5">
                  <span className="text-[12px]" style={{ color: "rgba(245,240,232,0.38)" }}>{c.role}</span>
                  <span style={{ color: "rgba(245,240,232,0.12)" }}>·</span>
                  <span className="text-[11px]" style={{ color: "rgba(245,240,232,0.22)", fontFamily: "var(--font-mono)" }}>
                    {new Date(c.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
                  </span>
                </div>
              </div>

              {/* Skills */}
              <div className="hidden lg:flex items-center gap-1.5 flex-shrink-0">
                {c.required_skills.slice(0, 3).map(s => (
                  <span
                    key={s}
                    className="text-[10.5px] px-2 py-0.5 rounded font-medium"
                    style={{ background: "rgba(255,248,235,0.04)", color: "rgba(245,240,232,0.45)", border: "1px solid rgba(255,248,235,0.07)" }}
                  >
                    {s}
                  </span>
                ))}
                {c.required_skills.length > 3 && (
                  <span className="text-[10.5px]" style={{ color: "rgba(245,240,232,0.25)" }}>
                    +{c.required_skills.length - 3}
                  </span>
                )}
              </div>

              {/* Actions */}
              <div className="flex items-center gap-1.5 flex-shrink-0">
                <Link
                  href={`/campaigns/${c.id}`}
                  className="flex items-center gap-1 text-[12px] font-medium px-3 py-1.5 rounded-lg opacity-0 group-hover:opacity-100 transition-all duration-150"
                  style={{ color: "#d97706", background: "rgba(217,119,6,0.08)", border: "1px solid rgba(217,119,6,0.15)" }}
                >
                  Open <ArrowUpRight size={11} />
                </Link>

                <div className="relative">
                  <button
                    onClick={() => setMenuOpen(menuOpen === c.id ? null : c.id)}
                    className="w-8 h-8 rounded-lg flex items-center justify-center transition-colors duration-150"
                    style={{ color: "rgba(245,240,232,0.3)", border: "1px solid transparent" }}
                    onMouseEnter={e => {
                      (e.currentTarget as HTMLElement).style.background = "rgba(255,248,235,0.05)";
                      (e.currentTarget as HTMLElement).style.color = "rgba(245,240,232,0.7)";
                    }}
                    onMouseLeave={e => {
                      (e.currentTarget as HTMLElement).style.background = "transparent";
                      (e.currentTarget as HTMLElement).style.color = "rgba(245,240,232,0.3)";
                    }}
                  >
                    <MoreHorizontal size={14} />
                  </button>
                  {menuOpen === c.id && (
                    <div
                      className="absolute right-0 top-full mt-1.5 z-20 rounded-xl overflow-hidden shadow-2xl"
                      style={{ background: "#1a1814", border: "1px solid rgba(255,248,235,0.1)", minWidth: "148px", boxShadow: "0 8px 32px rgba(0,0,0,0.5)" }}
                    >
                      <Link
                        href={`/campaigns/${c.id}`}
                        className="flex items-center gap-2.5 px-4 py-2.5 text-[12.5px] transition-colors"
                        style={{ color: "rgba(245,240,232,0.6)" }}
                        onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "rgba(255,248,235,0.05)"}
                        onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "transparent"}
                      >
                        <ArrowUpRight size={12} /> Open
                      </Link>
                      <div style={{ height: "1px", background: "rgba(255,248,235,0.05)" }} />
                      <button
                        onClick={() => handleDelete(c.id)}
                        className="w-full flex items-center gap-2.5 px-4 py-2.5 text-[12.5px] transition-colors"
                        style={{ color: "#f87171" }}
                        onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = "rgba(239,68,68,0.08)"}
                        onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = "transparent"}
                      >
                        <Trash2 size={12} /> Delete
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
