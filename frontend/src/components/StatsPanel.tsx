"use client";
import { CampaignStats } from "@/lib/api";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell
} from "recharts";

interface Props {
  stats: CampaignStats;
  campaignId: string;
}

export default function StatsPanel({ stats }: Props) {
  const pieData = [
    { name: "Strong Match",    value: stats.strong_match,   color: "#22c55e" },
    { name: "Moderate Match",  value: stats.moderate_match, color: "#6366f1" },
    { name: "Weak Match",      value: stats.weak_match,     color: "#f59e0b" },
    { name: "Rejected",        value: stats.rejected,       color: "#ef4444" },
  ].filter(d => d.value > 0);

  const skillData = stats.top_skills.slice(0, 8).map(s => ({
    name: s.skill.length > 14 ? s.skill.slice(0, 14) + "…" : s.skill,
    fullName: s.skill,
    count: s.count,
  }));

  const tooltipStyle = {
    contentStyle: {
      background: "#1a1814",
      border: "1px solid rgba(255,248,235,0.1)",
      borderRadius: 10,
      fontSize: 12,
      color: "#f5f0e8",
      fontFamily: "Geist, sans-serif",
      boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
    },
    labelStyle: { color: "rgba(245,240,232,0.45)", fontFamily: "Geist Mono, monospace" },
  };

  return (
    <div className="space-y-5">
      {/* ── Summary metrics ─────────────────────────────── */}
      <div className="grid grid-cols-5 gap-3">
        {[
          { label: "Total Uploaded", value: stats.total_resumes,  color: "rgba(245,240,232,0.6)" },
          { label: "Processed",      value: stats.total_processed, color: "#22c55e" },
          { label: "Pending",        value: stats.total_pending,   color: "#f59e0b" },
          { label: "Failed",         value: stats.total_failed,    color: "#ef4444" },
          { label: "Avg Score",      value: stats.avg_score ? stats.avg_score.toFixed(1) : "—", color: "#d97706" },
        ].map(m => (
          <div key={m.label} className="stat-card text-center">
            <div
              className="text-[24px] font-bold mb-1.5 leading-none tracking-tight tabular-nums"
              style={{ color: m.color, fontFamily: "var(--font-mono)" }}
            >
              {m.value}
            </div>
            <div className="text-[10.5px] font-medium" style={{ color: "rgba(245,240,232,0.32)", letterSpacing: "0.03em" }}>
              {m.label}
            </div>
          </div>
        ))}
      </div>

      {/* ── Charts row ───────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-4">
        {/* Pie chart */}
        <div
          className="rounded-2xl p-6"
          style={{ background: "#131210", border: "1px solid rgba(255,248,235,0.07)", boxShadow: "0 1px 3px rgba(0,0,0,0.2)" }}
        >
          <h3
            className="text-[10px] font-semibold uppercase tracking-widest mb-5"
            style={{ color: "rgba(245,240,232,0.28)", fontFamily: "var(--font-mono)" }}
          >
            Category Distribution
          </h3>
          {pieData.length === 0 ? (
            <div className="h-44 flex items-center justify-center text-[12px]" style={{ color: "rgba(245,240,232,0.22)" }}>
              No data yet
            </div>
          ) : (
            <div className="flex gap-6 items-center">
              <ResponsiveContainer width="55%" height={170}>
                <PieChart>
                  <Pie
                    data={pieData}
                    cx="50%"
                    cy="50%"
                    innerRadius={44}
                    outerRadius={72}
                    dataKey="value"
                    paddingAngle={3}
                    strokeWidth={0}
                  >
                    {pieData.map((entry, i) => (
                      <Cell key={i} fill={entry.color} opacity={0.9} />
                    ))}
                  </Pie>
                  <Tooltip {...tooltipStyle} />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex-1 space-y-2.5">
                {pieData.map(d => (
                  <div key={d.name} className="flex items-center gap-2.5">
                    <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: d.color }} />
                    <span className="text-[11.5px] flex-1" style={{ color: "rgba(245,240,232,0.5)" }}>{d.name}</span>
                    <span className="text-[13px] font-bold tabular-nums text-[#f5f0e8]" style={{ fontFamily: "var(--font-mono)" }}>
                      {d.value}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Bar chart */}
        <div
          className="rounded-2xl p-6"
          style={{ background: "#131210", border: "1px solid rgba(255,248,235,0.07)", boxShadow: "0 1px 3px rgba(0,0,0,0.2)" }}
        >
          <h3
            className="text-[10px] font-semibold uppercase tracking-widest mb-5"
            style={{ color: "rgba(245,240,232,0.28)", fontFamily: "var(--font-mono)" }}
          >
            Top Candidate Skills
          </h3>
          {skillData.length === 0 ? (
            <div className="h-44 flex items-center justify-center text-[12px]" style={{ color: "rgba(245,240,232,0.22)" }}>
              No data yet
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={170}>
              <BarChart data={skillData} layout="vertical" margin={{ left: 0, right: 24 }}>
                <XAxis
                  type="number"
                  tick={{ fill: "rgba(245,240,232,0.25)", fontSize: 10, fontFamily: "Geist Mono" }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  type="category"
                  dataKey="name"
                  tick={{ fill: "rgba(245,240,232,0.5)", fontSize: 11, fontFamily: "Geist Mono" }}
                  axisLine={false}
                  tickLine={false}
                  width={88}
                />
                <Tooltip
                  {...tooltipStyle}
                  cursor={{ fill: "rgba(217,119,6,0.05)" }}
                  formatter={(value, _, { payload }) => [value, payload.fullName]}
                />
                <Bar dataKey="count" fill="#d97706" radius={[0, 5, 5, 0]} opacity={0.85} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* ── Processing status breakdown ─────────────────── */}
      <div
        className="rounded-2xl p-6"
        style={{ background: "#131210", border: "1px solid rgba(255,248,235,0.07)", boxShadow: "0 1px 3px rgba(0,0,0,0.2)" }}
      >
        <h3
          className="text-[10px] font-semibold uppercase tracking-widest mb-5"
          style={{ color: "rgba(245,240,232,0.28)", fontFamily: "var(--font-mono)" }}
        >
          Processing Status
        </h3>
        <div className="space-y-4">
          {[
            { label: "Processed",   value: stats.total_processed, total: stats.total_resumes, color: "#22c55e" },
            { label: "In Progress", value: stats.total_pending,   total: stats.total_resumes, color: "#f59e0b" },
            { label: "Failed",      value: stats.total_failed,    total: stats.total_resumes, color: "#ef4444" },
          ].map(item => (
            <div key={item.label}>
              <div className="flex justify-between text-[12px] mb-2">
                <span style={{ color: item.color, fontWeight: 600 }}>{item.label}</span>
                <span style={{ color: "rgba(245,240,232,0.32)", fontFamily: "var(--font-mono)", fontSize: "11px" }}>
                  {item.value} / {item.total}
                </span>
              </div>
              <div
                className="h-1.5 rounded-full overflow-hidden"
                style={{ background: "rgba(255,248,235,0.05)" }}
              >
                <div
                  className="h-full rounded-full transition-all duration-700"
                  style={{
                    width: item.total ? `${(item.value / item.total) * 100}%` : "0%",
                    background: item.color,
                    opacity: 0.85,
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
