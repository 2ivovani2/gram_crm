"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import {
  LayoutDashboard, Users, Hash, MessageSquare, Zap,
  HelpCircle, LogOut, Send,
} from "lucide-react";

const NAV = [
  { href: "/dashboard",           icon: LayoutDashboard, label: "Дашборд" },
  { href: "/dashboard/accounts",  icon: Users,           label: "Аккаунты" },
  { href: "/dashboard/channels",  icon: Hash,            label: "Каналы" },
  { href: "/dashboard/messages",  icon: MessageSquare,   label: "Шаблоны" },
  { href: "/dashboard/campaigns", icon: Zap,             label: "Кампании" },
  { href: "/dashboard/help",      icon: HelpCircle,      label: "Помощь" },
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
    <div id="sidebar" className={`sidebar${open ? " open" : ""}`}>
      <Link href="/dashboard" className="sidebar-logo">
        <div className="sidebar-mark">
          <Send size={12} color="var(--p-fg)" strokeWidth={2.5} />
        </div>
        <span className="sidebar-wordmark">AutoSending</span>
      </Link>

      <nav className="sidebar-nav">
        {NAV.map(({ href, icon: Icon, label }) => {
          const active = isActive(pathname, href);
          return (
            <Link
              key={href}
              href={href}
              onClick={onClose}
              className={`sidebar-link${active ? " sidebar-link-active" : ""}`}
            >
              <Icon size={14} strokeWidth={active ? 2 : 1.75} />
              {label}
            </Link>
          );
        })}
      </nav>

      <div className="sidebar-user">
        <div className="sidebar-avatar">{initials}</div>
        <span className="sidebar-uname">{user?.username}</span>
        <button className="sidebar-logout" onClick={logout} title="Выйти">
          <LogOut size={12} />
        </button>
      </div>
    </div>
  );
}
