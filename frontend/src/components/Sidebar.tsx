"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Briefcase,
  Plus,
  Layers,
} from "lucide-react";
import clsx from "clsx";

const navItems = [
  { href: "/", icon: LayoutDashboard, label: "Dashboard", num: "01" },
  { href: "/campaigns", icon: Briefcase, label: "Campaigns", num: "02" },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="sidebar">
      {/* Logo */}
      <div className="sidebar-logo">
        <Link href="/" className="flex items-center gap-2.5">
          <div className="sidebar-logo-icon">
            <Layers size={15} className="text-white" strokeWidth={2.5} />
          </div>
          <div>
            <div className="sidebar-logo-title">ResumeAI</div>
            <div className="sidebar-logo-sub">Bulk Processing</div>
          </div>
        </Link>
      </div>

      {/* Nav */}
      <nav className="sidebar-nav">
        <div className="sidebar-section-label">Navigation</div>
        {navItems.map(({ href, icon: Icon, label, num }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link key={href} href={href}
              className={clsx("sidebar-nav-item", active && "active")}>
              <span
                className="text-[9px] font-mono font-semibold opacity-40 w-5 flex-shrink-0 tabular-nums"
                style={{ letterSpacing: "0.05em" }}
              >
                {num}
              </span>
              <Icon size={13} strokeWidth={active ? 2.5 : 2} />
              {label}
            </Link>
          );
        })}

        <div className="sidebar-section-label" style={{ marginTop: "20px" }}>Quick Actions</div>
        <Link href="/campaigns/new" className="sidebar-nav-item">
          <span className="text-[9px] font-mono font-semibold opacity-40 w-5 flex-shrink-0">↗</span>
          <Plus size={13} strokeWidth={2} />
          New Campaign
        </Link>
      </nav>

      {/* Footer */}
      <div className="sidebar-footer">
        <div className="flex items-center gap-2">
          <span className="relative flex h-1.5 w-1.5">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-60" />
            <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-400" />
          </span>
          <span style={{ fontSize: "11px", color: "rgba(245,240,232,0.28)", fontFamily: "var(--font-mono)", letterSpacing: "0.02em" }}>
            System operational
          </span>
        </div>
        <a href="http://localhost:5555" target="_blank" rel="noopener noreferrer"
          className="sidebar-flower-link">
          → Flower Dashboard
        </a>
      </div>
    </aside>
  );
}
