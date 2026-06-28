"use client";
import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  getCampaign, getCandidates, getCampaignStats, retryFailedResumes,
  Campaign, Candidate, CampaignStats
} from "@/lib/api";
import UploadZone from "@/components/UploadZone";
import CandidateTable from "@/components/CandidateTable";
import StatsPanel from "@/components/StatsPanel";
import {
  ArrowLeft, Upload, Users, BarChart3,
  RefreshCw, LucideIcon, AlertTriangle
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
  const [retrying, setRetrying] = useState(false);

  const [filters, setFilters] = useState({
    category: "",
    min_score: "",
    max_score: "",
    search: "",
    pipeline_stage: "",
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
    if (filters.pipeline_stage) params.pipeline_stage = filters.pipeline_stage;
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
    if (!loading) {
      void fetchCandidates();
    }
  }, [fetchCandidates, loading]);

  useEffect(() => {
    let interval: NodeJS.Timeout;
    if (stats && (stats.total_pending > 0)) {
      interval = setInterval(async () => {
        try {
          const newStats = await getCampaignStats(id);
          setStats(newStats);
        } catch (e) {}
      }, 5000);
    }
    return () => clearInterval(interval);
  }, [stats?.total_pending, id]);

  const refresh = async () => {
    setRefreshing(true);
    await Promise.all([fetchData(), fetchCandidates()]);
    setRefreshing(false);
  };

  const handleRetry = async () => {
    if (!stats) return;
    setRetrying(true);
    try {
      await retryFailedResumes(id);
      await refresh();
    } catch (e) {}
    finally {
      setRetrying(false);
    }
  };

  const tabs: { key: Tab; label: string; Icon: LucideIcon; count?: number }[] = [
    { key: "upload", label: "Upload", Icon: Upload },
    { key: "candidates", label: "Candidates", Icon: Users, count: stats?.total_processed },
    { key: "stats", label: "Analytics", Icon: BarChart3 },
  ];

  if (loading) return <LoadingSkeleton />;
  if (!campaign) return <div className="p-8" style={{ color: "rgba(245,240,232,0.4)" }}>Campaign not found</div>;

  return (
    <div className="page-wrapper min-h-screen">
      {/* ── Back + Header ─────────────────────────────── */}
      <div className="mb-10 slide-up">
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 text-[12px] font-medium mb-5 transition-colors"
          style={{ color: "rgba(245,240,232,0.32)" }}
          onMouseEnter={e => (e.currentTarget as HTMLElement).style.color = "rgba(245,240,232,0.65)"}
          onMouseLeave={e => (e.currentTarget as HTMLElement).style.color = "rgba(245,240,232,0.32)"}
        >
          <ArrowLeft size={13} strokeWidth={2} />
          Back to Dashboard
        </Link>

        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1.5">
              <span
                className="w-1.5 h-1.5 rounded-full"
                style={{ background: campaign.status === "active" ? "#22c55e" : "rgba(245,240,232,0.2)" }}
              />
              <span
                className="text-[10.5px] font-semibold uppercase tracking-widest"
                style={{ color: "rgba(245,240,232,0.28)", fontFamily: "var(--font-mono)" }}
              >
                {campaign.status} campaign
              </span>
            </div>
            <h1 className="text-[28px] font-bold text-[#f5f0e8] tracking-tight leading-tight">
              {campaign.title}
            </h1>
            <p className="text-[13px] mt-0.5" style={{ color: "rgba(245,240,232,0.4)" }}>
              {campaign.role}
            </p>
          </div>

          <button
            onClick={refresh}
            disabled={refreshing}
            className="btn-secondary"
            style={{ padding: "8px 12px" }}
          >
            <RefreshCw size={13} className={refreshing ? "animate-spin" : ""} />
          </button>
        </div>

        {/* Skills */}
        {campaign.required_skills?.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-4">
            {campaign.required_skills.map(s => (
              <span
                key={s}
                className="text-[10.5px] font-medium px-2 py-0.5 rounded"
                style={{ background: "rgba(255,248,235,0.04)", color: "rgba(245,240,232,0.5)", border: "1px solid rgba(255,248,235,0.07)" }}
              >
                {s}
              </span>
            ))}
          </div>
        )}

        {/* Warning banner */}
        {stats && (stats.total_quota_exceeded > 0 || stats.total_failed > 0) && (
          <div
            className="mt-5 p-4 rounded-xl flex items-center justify-between gap-4"
            style={{ background: "rgba(245,158,11,0.06)", border: "1px solid rgba(245,158,11,0.18)" }}
          >
            <div className="flex items-start gap-3">
              <AlertTriangle size={15} style={{ color: "#f59e0b", marginTop: "1px", flexShrink: 0 }} strokeWidth={2} />
              <div>
                <h4 className="text-[13px] font-semibold" style={{ color: "#f59e0b" }}>
                  {stats.total_quota_exceeded + stats.total_failed} resumes blocked or failed
                </h4>
                <p className="text-[12px] mt-0.5" style={{ color: "rgba(245,158,11,0.65)" }}>
                  Gemini API quota reached or parsing failed. You can retry now.
                </p>
              </div>
            </div>
            <button
              onClick={handleRetry}
              disabled={retrying}
              className="flex-shrink-0 px-4 py-2 rounded-lg text-[12px] font-semibold transition-all disabled:opacity-50"
              style={{ background: "rgba(245,158,11,0.12)", color: "#f59e0b", border: "1px solid rgba(245,158,11,0.25)" }}
            >
              {retrying ? "Retrying..." : "↻ Retry All"}
            </button>
          </div>
        )}

        {/* Quick stats bar */}
        {stats && (
          <div
            className="grid grid-cols-6 gap-3 mt-8 pt-8"
            style={{ borderTop: "1px solid rgba(255,248,235,0.05)" }}
          >
            {[
              { label: "Uploaded", value: stats.total_resumes, color: "rgba(245,240,232,0.65)" },
              { label: "Processed", value: stats.total_processed, color: "#22c55e" },
              { label: "In Queue", value: stats.total_pending, color: "#f59e0b" },
              { label: "Failed", value: stats.total_failed, color: "#ef4444" },
              { label: "Quota", value: stats.total_quota_exceeded, color: "#f97316" },
              { label: "Avg Score", value: stats.avg_score ? `${stats.avg_score.toFixed(1)}` : "—", color: "#a5b4fc" },
            ]
              .filter(s => !(s.label === "Quota" && s.value === 0))
              .map(s => (
                <div
                  key={s.label}
                  className="rounded-xl px-3 py-3 transition-colors"
                  style={{ background: "rgba(255,248,235,0.025)", border: "1px solid rgba(255,248,235,0.05)" }}
                >
                  <div className="text-[10px] font-medium mb-1.5" style={{ color: "rgba(245,240,232,0.32)", letterSpacing: "0.04em" }}>
                    {s.label}
                  </div>
                  <div className="text-[20px] font-bold tracking-tight leading-none" style={{ color: s.color }}>
                    {s.value}
                  </div>
                </div>
              ))}
          </div>
        )}
      </div>

      {/* ── Tabs ───────────────────────────────────────── */}
      <div
        className="flex gap-0.5 p-1 mb-8 rounded-xl w-fit slide-up"
        style={{ background: "rgba(255,248,235,0.03)", border: "1px solid rgba(255,248,235,0.06)", animationDelay: "0.04s" }}
      >
        {tabs.map(({ key, label, Icon, count }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={clsx(
              "flex items-center gap-2 px-4 py-2 rounded-lg text-[12.5px] font-medium transition-all duration-200",
              tab === key
                ? "text-[#f5f0e8]"
                : "hover:text-[rgba(245,240,232,0.6)]"
            )}
            style={tab === key
              ? { background: "rgba(255,248,235,0.08)", color: "#f5f0e8", boxShadow: "0 1px 3px rgba(0,0,0,0.2)" }
              : { color: "rgba(245,240,232,0.38)" }
            }
          >
            <Icon size={13} strokeWidth={2} />
            {label}
            {count !== undefined && count > 0 && (
              <span
                className="text-[10px] px-1.5 py-0.5 rounded-full font-semibold"
                style={{ background: "rgba(217,119,6,0.15)", color: "#d97706" }}
              >
                {count}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* ── Tab content ────────────────────────────────── */}
      <div className="slide-up" style={{ animationDelay: "0.08s" }}>
        {tab === "upload" && (
          <div className="max-w-2xl">
            <p className="text-[13px] mb-5" style={{ color: "rgba(245,240,232,0.38)", lineHeight: "1.6" }}>
              Drag and drop up to{" "}
              <strong style={{ color: "rgba(245,240,232,0.65)", fontWeight: 600 }}>50 PDF resumes</strong>{" "}
              at once. Processing happens automatically via AI.
            </p>

            {stats && stats.total_resumes > 0 && (
              <div
                className="mb-6 p-4 rounded-xl"
                style={{ border: "1px solid rgba(255,248,235,0.07)", background: "rgba(255,248,235,0.02)" }}
              >
                <div className="flex justify-between text-[12.5px] mb-3">
                  <span style={{ color: "rgba(245,240,232,0.6)", fontWeight: 500 }}>
                    {stats.total_resumes} total resumes
                  </span>
                  <div className="flex gap-4">
                    <span style={{ color: "#22c55e" }}>{stats.total_processed} done</span>
                    <span style={{ color: "#f59e0b" }}>{stats.total_pending} queued</span>
                    {(stats.total_failed + stats.total_quota_exceeded) > 0 && (
                      <span style={{ color: "#ef4444" }}>{stats.total_failed + stats.total_quota_exceeded} failed</span>
                    )}
                  </div>
                </div>
                <div className="w-full h-1.5 rounded-full overflow-hidden" style={{ background: "rgba(255,248,235,0.06)" }}>
                  <div
                    className="h-full rounded-full transition-all duration-700 ease-out"
                    style={{
                      width: `${(stats.total_processed / stats.total_resumes) * 100}%`,
                      background: "linear-gradient(90deg, #d97706, #fbbf24)",
                    }}
                  />
                </div>
              </div>
            )}

            <UploadZone campaignId={id} onProcessingComplete={refresh} />
          </div>
        )}
        {tab === "candidates" && (
          <CandidateTable
            campaignId={id}
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
    <div className="p-12 space-y-5 animate-pulse">
      <div className="h-4 rounded-lg w-32" style={{ background: "rgba(255,248,235,0.04)" }} />
      <div className="h-8 rounded-lg w-72" style={{ background: "rgba(255,248,235,0.04)" }} />
      <div className="flex gap-2 mt-4">
        {[...Array(5)].map((_, i) => (
          <div key={i} className="h-6 rounded-lg w-16" style={{ background: "rgba(255,248,235,0.04)" }} />
        ))}
      </div>
    </div>
  );
}
