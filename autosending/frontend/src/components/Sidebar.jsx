"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import {
  LayoutDashboard, Users, Hash, MessageSquare, Zap,
  HelpCircle, LogOut, Send, Search, Settings, Calculator,
} from "lucide-react";

const NAV = [
  { href: "/dashboard",              icon: LayoutDashboard, label: "Обзор" },
  { href: "/dashboard/accounts",     icon: Users,           label: "Аккаунты" },
  { href: "/dashboard/channels",     icon: Hash,            label: "Каналы" },
  { href: "/dashboard/messages",     icon: MessageSquare,   label: "Шаблоны" },
  { href: "/dashboard/campaigns",    icon: Zap,             label: "Кампании" },
  { href: "/dashboard/calculator",   icon: Calculator,      label: "Калькулятор" },
];
const NAV_SUPPORT = [
  { href: "/dashboard/help",      icon: HelpCircle,      label: "Документация" },
];

function isActive(pathname, href) {
  if (href === "/dashboard") return pathname === "/dashboard";
  return pathname.startsWith(href);
}

export default function Sidebar({ open, onClose }) {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const initials = user?.username ? user.username.slice(0, 2).toUpperCase() : "??";

  return (
    <aside id="sidebar" className={`sidebar${open ? " open" : ""}`}>
      <Link href="/dashboard" className="sidebar-logo">
        <div className="sidebar-mark">
          <Send size={14} strokeWidth={2.2} style={{ color: "var(--on-gold)", position: "relative", zIndex: 1 }} />
        </div>
        <div>
          <span className="sidebar-wordmark">AutoSending</span>
          <span className="sidebar-wordmark-tag">PRO</span>
        </div>
      </Link>

      <div className="sidebar-search">
        <div className="inp-wrap" style={{ position: "relative" }}>
          <Search
            size={13}
            style={{
              position: "absolute", left: 11, top: "50%",
              transform: "translateY(-50%)", color: "var(--ink-4)", pointerEvents: "none",
            }}
          />
          <input className="inp" placeholder="Поиск…" />
          <span className="sidebar-kbd"><span>⌘</span><span>K</span></span>
        </div>
      </div>

      <nav className="sidebar-nav">
        <div className="sidebar-section">Workspace</div>
        {NAV.map(({ href, icon: Icon, label }) => {
          const active = isActive(pathname, href);
          return (
            <Link
              key={href}
              href={href}
              onClick={onClose}
              className={`sidebar-link${active ? " sidebar-link-active" : ""}`}
            >
              <Icon size={15} strokeWidth={active ? 2 : 1.7} />
              {label}
            </Link>
          );
        })}

        <div className="sidebar-section">Поддержка</div>
        {NAV_SUPPORT.map(({ href, icon: Icon, label }) => {
          const active = isActive(pathname, href);
          return (
            <Link
              key={href}
              href={href}
              onClick={onClose}
              className={`sidebar-link${active ? " sidebar-link-active" : ""}`}
            >
              <Icon size={15} strokeWidth={active ? 2 : 1.7} />
              {label}
            </Link>
          );
        })}
      </nav>

      <div className="sidebar-user">
        <div className="sidebar-avatar">{initials}</div>
        <div className="sidebar-uname-wrap">
          <span className="sidebar-uname">{user?.username}</span>
          <span className="sidebar-uplan">PRO · workspace</span>
        </div>
        <button className="sidebar-logout" onClick={logout} title="Выйти">
          <LogOut size={13} />
        </button>
      </div>
    </aside>
  );
}
