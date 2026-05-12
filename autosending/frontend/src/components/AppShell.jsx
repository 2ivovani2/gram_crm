"use client";

import { useState, useCallback } from "react";
import { usePathname } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import Sidebar from "@/components/Sidebar";
import { Menu } from "lucide-react";

const PUBLIC_PATHS = ["/", "/login"];

const CRUMB_MAP = {
  "/dashboard/accounts":  "Аккаунты",
  "/dashboard/channels":  "Каналы",
  "/dashboard/messages":  "Шаблоны",
  "/dashboard/campaigns": "Кампании",
  "/dashboard/help":      "Помощь",
};

function getBreadcrumb(pathname) {
  for (const [prefix, label] of Object.entries(CRUMB_MAP)) {
    if (pathname.startsWith(prefix)) return label;
  }
  return "Дашборд";
}

export default function AppShell({ children }) {
  const { user } = useAuth();
  const pathname = usePathname();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const closeSidebar = useCallback(() => setSidebarOpen(false), []);
  const toggleSidebar = useCallback(() => setSidebarOpen(v => !v), []);

  const isPublic = PUBLIC_PATHS.includes(pathname);

  if (isPublic || !user) {
    return <>{children}</>;
  }

  const crumb = getBreadcrumb(pathname);
  const initials = user?.username ? user.username.slice(0, 2).toUpperCase() : "??";

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      {/* Mobile overlay */}
      <div
        className={`sidebar-overlay${sidebarOpen ? " open" : ""}`}
        onClick={closeSidebar}
      />

      <Sidebar open={sidebarOpen} onClose={closeSidebar} />

      <main className="main" style={{ flex: 1, marginLeft: "var(--sidebar-w)", minHeight: "100vh", minWidth: 0, display: "flex", flexDirection: "column" }}>
        {/* Topbar */}
        <header className="topbar">
          <button
            className="topbar-burger"
            onClick={toggleSidebar}
            aria-label="Открыть меню"
          >
            <Menu size={16} />
          </button>

          <nav className="topbar-crumb">
            <span>AutoSending</span>
            <span>›</span>
            <span style={{ color: "var(--text-2)" }}>{crumb}</span>
          </nav>

          <div style={{ flex: 1 }} />

          <div className="topbar-user">
            <div className="topbar-user-avatar">{initials}</div>
            <span className="topbar-uname">{user?.username}</span>
          </div>
        </header>

        {/* Page content */}
        <div style={{ padding: "28px 32px", maxWidth: 1080, margin: "0 auto", width: "100%" }}>
          {children}
        </div>
      </main>
    </div>
  );
}
