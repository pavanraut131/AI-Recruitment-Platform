"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Briefcase,
  Upload,
  Zap,
} from "lucide-react";
import clsx from "clsx";

const navItems = [
  { href: "/", icon: LayoutDashboard, label: "Dashboard" },
  { href: "/campaigns", icon: Briefcase, label: "Campaigns" },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="sidebar">
      {/* Logo */}
      <div className="sidebar-logo">
        <Link href="/" className="flex items-center gap-3">
          <div className="sidebar-logo-icon">
            <Zap size={18} className="text-white" />
          </div>
          <div>
            <div className="sidebar-logo-title">ResumeAI</div>
            <div className="sidebar-logo-sub">Bulk Processing</div>
          </div>
        </Link>
      </div>

      {/* Nav */}
      <nav className="sidebar-nav">
        <div className="sidebar-section-label">Main</div>
        {navItems.map(({ href, icon: Icon, label }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link key={href} href={href}
              className={clsx("sidebar-nav-item", active && "active")}>
              <Icon size={16} />
              {label}
            </Link>
          );
        })}

        <div className="sidebar-section-label" style={{ marginTop: "20px" }}>Quick Actions</div>
        <Link href="/campaigns/new" className="sidebar-nav-item">
          <Upload size={16} />
          New Campaign
        </Link>
      </nav>

      {/* Footer */}
      <div className="sidebar-footer">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
          <span style={{ fontSize: "12px", color: "rgba(255,255,255,0.3)" }}>System operational</span>
        </div>
        <a href="http://localhost:5555" target="_blank" rel="noopener noreferrer"
          className="sidebar-flower-link">
          → Flower Dashboard
        </a>
      </div>
    </aside>
  );
}
