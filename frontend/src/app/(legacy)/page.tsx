"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { getCampaigns, getGlobalStats, Campaign, GlobalStats } from "@/lib/api";
import {
  Briefcase, Plus, Users, Clock,
  TrendingUp, ArrowUpRight, Upload
} from "lucide-react";

export default function DashboardPage() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [globalStats, setGlobalStats] = useState<GlobalStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([getCampaigns(), getGlobalStats()])
      .then(([campData, statsData]) => {
        setCampaigns(campData);
        setGlobalStats(statsData);
      })
      .finally(() => setLoading(false));
  }, []);

  const stats = [
    { label: "Total Campaigns", value: globalStats?.total_campaigns ?? 0, icon: Briefcase, accent: "#d97706" },
    { label: "Active", value: globalStats?.active_campaigns ?? 0, icon: TrendingUp, accent: "#22c55e" },
    { label: "Processing", value: globalStats?.total_processing ?? 0, icon: Clock, accent: "#6366f1", pulse: (globalStats?.total_processing ?? 0) > 0 },
    { label: "Candidates", value: globalStats?.total_candidates ?? 0, icon: Users, accent: "#f59e0b" },
  ];

  return (
    <div className="page-wrapper">
      {/* ── Header ─────────────────────────────────────── */}
      <div className="flex items-end justify-between mb-12 slide-up">
        <div>
          <div className="page-eyebrow mb-3">
            <span style={{ opacity: 0.5 }}>—</span>
            AI Recruitment Platform
          </div>
          <h1 className="text-[32px] font-bold tracking-tight text-[#f5f0e8] leading-none">
            Dashboard
          </h1>
          <p className="text-[13px] mt-2" style={{ color: "rgba(245,240,232,0.38)" }}>
            Manage hiring campaigns and track your candidate pipeline
          </p>
        </div>
        <Link href="/campaigns/new" className="btn-primary">
          <Plus size={14} strokeWidth={2.5} />
          New Campaign
        </Link>
      </div>

      {/* ── Stats row ─────────────────────────────────── */}
      <div
        className="grid grid-cols-4 gap-4 mb-14 slide-up"
        style={{ animationDelay: "0.04s" }}
      >
        {stats.map((s) => (
          <div
            key={s.label}
            className="stat-card"
            style={{ borderTop: `1.5px solid ${s.accent}30` }}
          >
            <div className="flex items-center justify-between mb-4">
              <div
                className="w-8 h-8 rounded-lg flex items-center justify-center"
                style={{ background: `${s.accent}12`, border: `1px solid ${s.accent}20` }}
              >
                <s.icon size={15} style={{ color: s.accent }} strokeWidth={2} />
              </div>
              {s.pulse && (
                <span className="relative flex h-1.5 w-1.5">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style={{ background: s.accent }} />
                  <span className="relative inline-flex rounded-full h-1.5 w-1.5" style={{ background: s.accent }} />
                </span>
              )}
            </div>
            <div className="text-[26px] font-bold text-[#f5f0e8] leading-none tracking-tight mb-1">
              {s.value}
            </div>
            <div className="text-[11.5px]" style={{ color: "rgba(245,240,232,0.38)", letterSpacing: "0.01em" }}>
              {s.label}
            </div>
          </div>
        ))}
      </div>

      {/* ── Campaigns section ─────────────────────────── */}
      <div className="slide-up" style={{ animationDelay: "0.08s" }}>
        <div className="flex items-baseline justify-between mb-6">
          <div className="flex items-baseline gap-3">
            <h2 className="text-[17px] font-bold text-[#f5f0e8] tracking-tight">Campaigns</h2>
            {campaigns.length > 0 && (
              <span
                className="text-[11px] font-semibold rounded-full px-2 py-0.5"
                style={{ background: "rgba(217,119,6,0.12)", color: "#d97706", border: "1px solid rgba(217,119,6,0.2)" }}
              >
                {campaigns.length}
              </span>
            )}
          </div>
          <Link
            href="/campaigns"
            className="flex items-center gap-1 text-[12px] font-medium transition-colors"
            style={{ color: "rgba(245,240,232,0.38)" }}
          >
            View all <ArrowUpRight size={12} />
          </Link>
        </div>

        {loading ? (
          <div className="grid grid-cols-2 gap-4">
            {[...Array(4)].map((_, i) => (
              <div
                key={i}
                className="h-44 rounded-2xl animate-pulse"
                style={{ background: "rgba(255,248,235,0.025)", border: "1px solid rgba(255,248,235,0.05)" }}
              />
            ))}
          </div>
        ) : campaigns.length === 0 ? (
          <EmptyState />
        ) : (
          <div className="grid grid-cols-2 gap-4">
            {campaigns.map((c, i) => (
              <CampaignCard key={c.id} campaign={c} index={i} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function CampaignCard({ campaign, index }: { campaign: Campaign; index: number }) {
  return (
    <Link
      href={`/campaigns/${campaign.id}`}
      className="glass-card p-5 group block slide-up"
      style={{ animationDelay: `${0.04 * index}s` }}
    >
      <div className="flex items-start justify-between mb-4">
        <div className="flex-1 min-w-0">
          {/* Status */}
          <div className="flex items-center gap-1.5 mb-2.5">
            <span
              className="w-1.5 h-1.5 rounded-full flex-shrink-0"
              style={{ background: campaign.status === "active" ? "#22c55e" : "rgba(245,240,232,0.2)" }}
            />
            <span
              className="text-[10px] font-semibold uppercase tracking-widest"
              style={{ color: campaign.status === "active" ? "#22c55e" : "rgba(245,240,232,0.3)", letterSpacing: "0.1em" }}
            >
              {campaign.status}
            </span>
          </div>
          <h3 className="font-bold text-[15px] text-[#f5f0e8] tracking-tight truncate group-hover:text-[#fbbf24] transition-colors duration-200">
            {campaign.title}
          </h3>
          <p className="text-[12px] mt-0.5" style={{ color: "rgba(245,240,232,0.38)" }}>
            {campaign.role}
          </p>
        </div>
        <div
          className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 ml-3 transition-all duration-200 group-hover:border-[rgba(217,119,6,0.35)] group-hover:bg-[rgba(217,119,6,0.08)]"
          style={{ background: "rgba(255,248,235,0.03)", border: "1px solid rgba(255,248,235,0.07)" }}
        >
          <ArrowUpRight size={13} className="text-[rgba(245,240,232,0.25)] group-hover:text-[#d97706] transition-colors duration-200" />
        </div>
      </div>

      {/* Skills */}
      {campaign.required_skills?.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-4">
          {campaign.required_skills.slice(0, 4).map(skill => (
            <span
              key={skill}
              className="text-[10.5px] font-medium px-2 py-0.5 rounded"
              style={{ background: "rgba(255,248,235,0.04)", color: "rgba(245,240,232,0.5)", border: "1px solid rgba(255,248,235,0.07)" }}
            >
              {skill}
            </span>
          ))}
          {campaign.required_skills.length > 4 && (
            <span
              className="text-[10.5px] font-medium px-2 py-0.5 rounded"
              style={{ background: "rgba(255,248,235,0.02)", color: "rgba(245,240,232,0.28)", border: "1px solid rgba(255,248,235,0.05)" }}
            >
              +{campaign.required_skills.length - 4} more
            </span>
          )}
        </div>
      )}

      <div
        className="flex items-center justify-between pt-3.5"
        style={{ borderTop: "1px solid rgba(255,248,235,0.05)" }}
      >
        <span className="text-[11px]" style={{ color: "rgba(245,240,232,0.25)" }}>
          {new Date(campaign.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
        </span>
        <span
          className="flex items-center gap-1.5 text-[11px] font-semibold"
          style={{ color: "rgba(217,119,6,0.7)" }}
        >
          <Upload size={10} />
          Upload Resumes
        </span>
      </div>
    </Link>
  );
}

function EmptyState() {
  return (
    <div
      className="rounded-2xl p-20 text-center"
      style={{
        border: "1px dashed rgba(255,248,235,0.07)",
        background: "rgba(255,248,235,0.01)",
      }}
    >
      <div
        className="w-14 h-14 rounded-2xl mx-auto mb-6 inline-flex items-center justify-center"
        style={{ background: "rgba(217,119,6,0.08)", border: "1px solid rgba(217,119,6,0.16)" }}
      >
        <Briefcase size={24} style={{ color: "#d97706" }} />
      </div>
      <h3 className="text-[18px] font-bold text-[#f5f0e8] mb-2.5 tracking-tight">No campaigns yet</h3>
      <p
        className="text-[13px] mb-8 max-w-xs mx-auto leading-relaxed"
        style={{ color: "rgba(245,240,232,0.38)" }}
      >
        Create your first hiring campaign to start uploading and analyzing resumes.
      </p>
      <Link href="/campaigns/new" className="btn-primary">
        <Plus size={15} />
        Create First Campaign
      </Link>
    </div>
  );
}
