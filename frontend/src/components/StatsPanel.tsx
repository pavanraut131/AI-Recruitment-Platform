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
    { name: "Strong Match", value: stats.strong_match, color: "#10b981" },
    { name: "Moderate Match", value: stats.moderate_match, color: "#06b6d4" },
    { name: "Weak Match", value: stats.weak_match, color: "#f59e0b" },
    { name: "Rejected", value: stats.rejected, color: "#ef4444" },
  ].filter(d => d.value > 0);

  const skillData = stats.top_skills.slice(0, 8).map(s => ({
    name: s.skill.length > 12 ? s.skill.slice(0, 12) + "…" : s.skill,
    fullName: s.skill,
    count: s.count,
  }));

  return (
    <div className="space-y-6">
      {/* Summary metrics */}
      <div className="grid grid-cols-5 gap-3">
        {[
          { label: "Total Uploaded", value: stats.total_resumes, color: "#94a3b8" },
          { label: "Processed", value: stats.total_processed, color: "#10b981" },
          { label: "Pending", value: stats.total_pending, color: "#f59e0b" },
          { label: "Failed", value: stats.total_failed, color: "#ef4444" },
          { label: "Avg Score", value: stats.avg_score ? `${stats.avg_score.toFixed(1)}` : "—", color: "#7c3aed" },
        ].map(m => (
          <div key={m.label} className="stat-card text-center">
            <div className="text-2xl font-bold mb-1" style={{ color: m.color }}>{m.value}</div>
            <div className="text-[11px] text-white/35">{m.label}</div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-6">
        {/* Pie chart - category distribution */}
        <div className="glass-card p-6">
          <h3 className="text-[13px] font-semibold text-white/60 uppercase tracking-wider mb-5">
            Category Distribution
          </h3>
          {pieData.length === 0 ? (
            <div className="h-48 flex items-center justify-center text-white/25 text-[13px]">
              No data yet
            </div>
          ) : (
            <div className="flex gap-4 items-center">
              <ResponsiveContainer width="60%" height={180}>
                <PieChart>
                  <Pie data={pieData} cx="50%" cy="50%" innerRadius={45} outerRadius={75}
                    dataKey="value" paddingAngle={3}>
                    {pieData.map((entry, i) => (
                      <Cell key={i} fill={entry.color} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{ background: "#0f1117", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 8, fontSize: 12 }}
                    labelStyle={{ color: "#94a3b8" }}
                  />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex-1 space-y-2">
                {pieData.map(d => (
                  <div key={d.name} className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: d.color }} />
                    <span className="text-[12px] text-white/50 flex-1">{d.name}</span>
                    <span className="text-[13px] font-semibold text-white">{d.value}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Bar chart - top skills */}
        <div className="glass-card p-6">
          <h3 className="text-[13px] font-semibold text-white/60 uppercase tracking-wider mb-5">
            Top Candidate Skills
          </h3>
          {skillData.length === 0 ? (
            <div className="h-48 flex items-center justify-center text-white/25 text-[13px]">
              No data yet
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={skillData} layout="vertical" margin={{ left: 0, right: 20 }}>
                <XAxis type="number" tick={{ fill: "#64748b", fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis type="category" dataKey="name" tick={{ fill: "#94a3b8", fontSize: 11 }} axisLine={false} tickLine={false} width={80} />
                <Tooltip
                  contentStyle={{ background: "#0f1117", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 8, fontSize: 12 }}
                  cursor={{ fill: "rgba(124,58,237,0.08)" }}
                  formatter={(value, _, { payload }) => [value, payload.fullName]}
                />
                <Bar dataKey="count" fill="#7c3aed" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* Processing status breakdown */}
      <div className="glass-card p-6">
        <h3 className="text-[13px] font-semibold text-white/60 uppercase tracking-wider mb-4">
          Processing Status
        </h3>
        <div className="space-y-3">
          {[
            { label: "Processed", value: stats.total_processed, total: stats.total_resumes, color: "#10b981" },
            { label: "In Progress", value: stats.total_pending, total: stats.total_resumes, color: "#f59e0b" },
            { label: "Failed", value: stats.total_failed, total: stats.total_resumes, color: "#ef4444" },
          ].map(item => (
            <div key={item.label}>
              <div className="flex justify-between text-[12px] mb-1">
                <span style={{ color: item.color }}>{item.label}</span>
                <span className="text-white/40">{item.value} / {item.total}</span>
              </div>
              <div className="progress-bar">
                <div className="progress-fill"
                  style={{
                    width: item.total ? `${(item.value / item.total) * 100}%` : "0%",
                    background: item.color,
                  }} />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
