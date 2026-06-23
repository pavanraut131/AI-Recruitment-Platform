"use client";
import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  getCampaign, getCandidates, getCampaignStats,
  Campaign, Candidate, CampaignStats
} from "@/lib/api";
import UploadZone from "@/components/UploadZone";
import CandidateTable from "@/components/CandidateTable";
import StatsPanel from "@/components/StatsPanel";
import {
  ArrowLeft, Upload, Users, BarChart3,
  RefreshCw, LucideIcon
} from "lucide-react";
import clsx from "clsx";

type Tab = "upload" | "candidates" | "stats";

export default function CampaignPage() {
  const { id } = useParams<{ id: string }>();
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [stats, setStats] = useState<CampaignStats | null>(null);
  const [tab, setTab] = useState<Tab>("upload");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const [filters, setFilters] = useState({
    category: "",
    min_score: "",
    max_score: "",
    search: "",
  });

  const fetchData = useCallback(async () => {
    try {
      const [c, s] = await Promise.all([
        getCampaign(id),
        getCampaignStats(id),
      ]);
      setCampaign(c);
      setStats(s);
    } catch {}
  }, [id]);

  const fetchCandidates = useCallback(async () => {
    const params: Record<string, string | number> = {};
    if (filters.category) params.category = filters.category;
    if (filters.min_score) params.min_score = Number(filters.min_score);
    if (filters.max_score) params.max_score = Number(filters.max_score);
    if (filters.search) params.search = filters.search;
    try {
      const data = await getCandidates(id, params);
      setCandidates(data);
    } catch {}
  }, [id, filters]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/exhaustive-deps
    void (async () => {
      setLoading(true);
      await Promise.all([fetchData(), fetchCandidates()]);
      setLoading(false);
    })();
  }, [id]); // intentionally only on id change

  useEffect(() => {
    // Re-fetch candidates when filters change, but skip the initial mount
    // since the first useEffect handles the initial load
    if (!loading) {
      void fetchCandidates();
    }
  }, [fetchCandidates, loading]);


  const refresh = async () => {
    setRefreshing(true);
    await Promise.all([fetchData(), fetchCandidates()]);
    setRefreshing(false);
  };

  const tabs: { key: Tab; label: string; Icon: LucideIcon; count?: number }[] = [
    { key: "upload", label: "Upload", Icon: Upload },
    { key: "candidates", label: "Candidates", Icon: Users, count: stats?.total_processed },
    { key: "stats", label: "Analytics", Icon: BarChart3 },
  ];

  if (loading) return <LoadingSkeleton />;
  if (!campaign) return <div className="p-8 text-white/40">Campaign not found</div>;

  return (
    <div className="page-wrapper min-h-screen">
      {/* Header */}
      <div className="mb-8 slide-up">
        <Link href="/" className="flex items-center gap-2 text-white/40 hover:text-white/70 text-sm mb-5 transition-colors w-fit">
          <ArrowLeft size={15} /> Back to Dashboard
        </Link>

        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className={clsx(
                "w-1.5 h-1.5 rounded-full",
                campaign.status === "active" ? "bg-emerald-400" : "bg-white/20"
              )} />
              <span className="text-[11px] font-semibold uppercase tracking-widest text-white/30">
                {campaign.status} campaign
              </span>
            </div>
            <h1 className="text-2xl font-bold text-white">{campaign.title}</h1>
            <p className="text-white/40 text-sm mt-0.5">{campaign.role}</p>
          </div>

          <div className="flex items-center gap-2">
            <button onClick={refresh} disabled={refreshing}
              className="btn-secondary py-2 px-3">
              <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} />
            </button>
          </div>
        </div>

        {/* Skills */}
        {campaign.required_skills?.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-4">
            {campaign.required_skills.map(s => (
              <span key={s} className="text-[11px] px-2.5 py-1 rounded-lg text-white/50"
                style={{ background: "rgba(124,58,237,0.08)", border: "1px solid rgba(124,58,237,0.18)" }}>
                {s}
              </span>
            ))}
          </div>
        )}

        {/* Quota Exceeded Banner */}
        {stats?.total_quota_exceeded > 0 && (
          <div className="mt-4 p-4 rounded-xl bg-orange-500/10 border border-orange-500/20 flex items-start gap-3">
            <div className="text-orange-400 mt-0.5">⚠️</div>
            <div>
              <h4 className="text-sm font-semibold text-orange-400">API Quota Reached</h4>
              <p className="text-[13px] text-orange-400/80 mt-0.5">
                {stats.total_quota_exceeded} resume(s) could not be processed because the Gemini free tier limit (20/day) was reached. 
                They will automatically be retried tomorrow, or you can upgrade to a paid API key.
              </p>
            </div>
          </div>
        )}

        {/* Quick stats bar */}
        {stats && (
          <div className="grid grid-cols-6 gap-4 mt-8 pt-8 border-t border-white/5">
            {[
              { label: "Total Uploaded", value: stats.total_resumes, color: "#94a3b8" },
              { label: "Processed", value: stats.total_processed, color: "#10b981" },
              { label: "In Queue", value: stats.total_pending, color: "#f59e0b" },
              { label: "Failed", value: stats.total_failed, color: "#ef4444" },
              { label: "Quota Blocked", value: stats.total_quota_exceeded, color: "#f97316" },
              { label: "Avg Score", value: stats.avg_score ? `${stats.avg_score.toFixed(1)}/100` : "—", color: "#a78bfa" },
            ].map(s => (
              <div key={s.label} className={clsx("bg-white/2 border border-white/5 rounded-xl px-4 py-3 group hover:border-white/10 transition-colors", s.value === 0 && s.label === "Quota Blocked" && "hidden")}>
                <div className="text-[11px] text-white/35 mb-1.5 font-medium">{s.label}</div>
                <div className="text-xl font-bold tracking-tight" style={{ color: s.color }}>{s.value}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 p-1 mb-8 rounded-xl w-fit slide-up"
        style={{ background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.07)", animationDelay: "0.05s" }}>
        {tabs.map(({ key, label, Icon, count }) => (
          <button key={key} onClick={() => setTab(key)}
            className={clsx(
              "flex items-center gap-2 px-4 py-2 rounded-lg text-[13px] font-medium transition-all duration-200",
              tab === key
                ? "bg-white/10 text-white shadow-sm"
                : "text-white/40 hover:text-white/70"
            )}>
            <Icon size={14} />
            {label}
            {count !== undefined && count > 0 && (
              <span className="text-[10px] px-1.5 py-0.5 rounded-full font-semibold"
                style={{ background: "rgba(124,58,237,0.2)", color: "#a78bfa" }}>
                {count}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="slide-up" style={{ animationDelay: "0.1s" }}>
        {tab === "upload" && (
          <div className="max-w-2xl">
            <p className="text-white/40 text-[13px] mb-4">
              Drag and drop up to <strong className="text-white/60">50 PDF resumes</strong> at once.
              Processing happens automatically — watch live status updates below.
            </p>
            <UploadZone campaignId={id} onProcessingComplete={refresh} />
          </div>
        )}
        {tab === "candidates" && (
          <CandidateTable
            candidates={candidates}
            filters={filters}
            onFilterChange={(f) => setFilters(prev => ({ ...prev, ...f }))}
            onRefresh={fetchCandidates}
          />
        )}
        {tab === "stats" && stats && (
          <StatsPanel stats={stats} campaignId={id} />
        )}
      </div>
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="p-8 space-y-6 animate-pulse">
      <div className="h-6 bg-white/5 rounded w-64" />
      <div className="h-10 bg-white/5 rounded w-96" />
      <div className="flex gap-2">
        {[...Array(5)].map((_, i) => <div key={i} className="h-8 bg-white/5 rounded w-20" />)}
      </div>
    </div>
  );
}
