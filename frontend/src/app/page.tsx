"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { getCampaigns, Campaign } from "@/lib/api";
import {
  Briefcase, Plus, Users, Clock,
  TrendingUp, ArrowRight, Sparkles, Upload
} from "lucide-react";

export default function DashboardPage() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getCampaigns()
      .then(setCampaigns)
      .finally(() => setLoading(false));
  }, []);

  const totalCampaigns = campaigns.length;
  const totalActive = campaigns.filter(c => c.status === "active").length;

  return (
    <div className="page-wrapper">
      {/* Header */}
      <div className="flex items-center justify-between mb-10 slide-up">
        <div>
          <div className="flex items-center gap-2 mb-2">
            <Sparkles size={16} className="text-violet-400" />
            <span className="text-[12px] font-semibold text-violet-400 uppercase tracking-widest">
              AI Recruitment Platform
            </span>
          </div>
          <h1 className="text-3xl font-bold text-white">Dashboard</h1>
          <p className="text-white/40 text-sm mt-1.5">Manage your hiring campaigns and candidate pipeline</p>
        </div>
        <Link href="/campaigns/new" className="btn-primary">
          <Plus size={16} />
          New Campaign
        </Link>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-4 gap-6 mb-12 slide-up" style={{ animationDelay: "0.05s" }}>
        {[
          { label: "Total Campaigns", value: totalCampaigns, icon: Briefcase, color: "#7c3aed" },
          { label: "Active Campaigns", value: totalActive, icon: TrendingUp, color: "#06b6d4" },
          { label: "Processing", value: "—", icon: Clock, color: "#f59e0b" },
          { label: "Candidates Found", value: "—", icon: Users, color: "#10b981" },
        ].map((s) => (
          <div key={s.label} className="stat-card group" style={{ borderTop: `2px solid ${s.color}40` }}>
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-xl flex items-center justify-center transition-all duration-300 group-hover:scale-110 flex-shrink-0"
                style={{ background: `${s.color}15`, border: `1px solid ${s.color}25` }}>
                <s.icon size={20} style={{ color: s.color }} />
              </div>
              <div>
                <span className="block text-[13px] text-white/40 font-medium group-hover:text-white/60 transition-colors mb-0.5">{s.label}</span>
                <div className="text-2xl font-bold text-white tracking-tight leading-none">{s.value}</div>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Campaigns Grid */}
      <div className="slide-up mt-6" style={{ animationDelay: "0.1s" }}>
        <div className="flex items-center justify-between mb-8">
          <h2 className="text-xl font-bold text-white tracking-tight">Campaigns</h2>
          <Link href="/campaigns" className="text-[13px] text-violet-400 hover:text-violet-300 transition-colors font-medium">
            View all →
          </Link>
        </div>

        {loading ? (
          <div className="grid grid-cols-2 gap-4">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="glass-card p-6 h-48 animate-pulse">
                <div className="h-4 bg-white/5 rounded w-3/4 mb-3" />
                <div className="h-3 bg-white/5 rounded w-1/2 mb-6" />
                <div className="flex gap-2">
                  {[...Array(3)].map((_, j) => (
                    <div key={j} className="h-6 bg-white/5 rounded-full w-16" />
                  ))}
                </div>
              </div>
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
    <Link href={`/campaigns/${campaign.id}`}
      className="glass-card p-6 group block slide-up"
      style={{ animationDelay: `${0.05 * index}s` }}>
      <div className="flex items-start justify-between mb-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-2">
            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider ${campaign.status === "active"
              ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/15"
              : "bg-white/5 text-white/45 border border-white/10"
              }`}>
              <span className={`w-1 h-1 rounded-full ${campaign.status === "active" ? "bg-emerald-400 animate-pulse" : "bg-white/30"}`} />
              {campaign.status}
            </span>
          </div>
          <h3 className="font-bold text-white text-[16px] tracking-tight truncate group-hover:text-violet-300 transition-colors">
            {campaign.title}
          </h3>
          <p className="text-[13px] text-white/40 mt-0.5 font-medium">{campaign.role}</p>
        </div>
        <div className="w-8 h-8 rounded-lg flex items-center justify-center bg-white/3 border border-white/5 group-hover:border-violet-500/30 group-hover:bg-violet-500/10 transition-all flex-shrink-0">
          <ArrowRight size={14} className="text-white/30 group-hover:text-violet-400 group-hover:translate-x-0.5 transition-all" />
        </div>
      </div>

      {/* Skills */}
      {campaign.required_skills?.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-5">
          {campaign.required_skills.slice(0, 4).map(skill => (
            <span key={skill}
              className="text-[11px] font-medium px-2.5 py-0.5 rounded-md text-violet-300 transition-colors"
              style={{ background: "rgba(124,58,237,0.08)", border: "1px solid rgba(124,58,237,0.18)" }}>
              {skill}
            </span>
          ))}
          {campaign.required_skills.length > 4 && (
            <span className="text-[11px] font-medium px-2 py-0.5 rounded-md text-white/40"
              style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}>
              +{campaign.required_skills.length - 4}
            </span>
          )}
        </div>
      )}

      <div className="flex items-center justify-between pt-4 border-t border-white/5">
        <span className="text-[11px] text-white/30 font-medium">
          Created {new Date(campaign.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
        </span>
        <span className="flex items-center gap-1 text-[12px] font-semibold text-violet-400 group-hover:text-violet-300 transition-colors">
          <Upload size={12} />
          Upload Resumes
        </span>
      </div>
    </Link>
  );
}

function EmptyState() {
  return (
    <div className="border-2 border-dashed border-white/5 rounded-3xl p-24 text-center bg-gradient-to-b from-white/[0.02] to-transparent mt-4">
      <div className="w-16 h-16 rounded-2xl mx-auto mb-8 mt-[12px] inline-flex items-center justify-center shadow-[0_0_40px_rgba(124,58,237,0.15)] transition-all hover:scale-105"
        style={{ background: "rgba(124,58,237,0.1)", border: "1px solid rgba(124,58,237,0.2)" }}>
        <Briefcase size={28} className="text-violet-400" />
      </div>
      <h3 style={{ padding: "24px" }} className="text-xl font-bold text-white mb-3 tracking-tight ">No campaigns yet</h3>
      <p className="text-white/40 text-[15px] mb-8 max-w-sm mx-auto leading-relaxed">
        Create your first hiring campaign to start uploading and analyzing resumes with our AI recruitment engine.
      </p>
      <Link href="/campaigns/new" className="btn-primary shadow-lg shadow-violet-500/20 px-8 py-3.5 text-[14px]">
        <Plus size={18} />
        Create First Campaign
      </Link>
    </div>
  );
}
