"use client";

import { useState, useCallback } from "react";
import { usePathname } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import Sidebar from "@/components/Sidebar";
import { Menu, Bell, LifeBuoy, ChevronRight } from "lucide-react";

const PUBLIC_PATHS = ["/", "/login"];

const CRUMB_MAP = {
  "/dashboard/accounts":  "Аккаунты",
  "/dashboard/channels":  "Каналы",
  "/dashboard/messages":  "Шаблоны",
  "/dashboard/campaigns": "Кампании",
  "/dashboard/help":      "Документация",
};

function getBreadcrumb(pathname) {
  for (const [prefix, label] of Object.entries(CRUMB_MAP)) {
    if (pathname.startsWith(prefix)) return label;
  }
  return "Обзор";
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

  return (
    <div style={{ display: "flex", minHeight: "100vh", position: "relative" }}>
      {/* Mobile overlay */}
      <div
        className={`sidebar-overlay${sidebarOpen ? " open" : ""}`}
        onClick={closeSidebar}
      />

      <Sidebar open={sidebarOpen} onClose={closeSidebar} />

      <main
        className="main"
        style={{
          flex: 1,
          marginLeft: "var(--sidebar-w)",
          minHeight: "100vh",
          minWidth: 0,
          display: "flex",
          flexDirection: "column",
          position: "relative",
          zIndex: 1,
        }}
      >
        <header className="topbar">
          <button
            className="topbar-burger"
            onClick={toggleSidebar}
            aria-label="Открыть меню"
          >
            <Menu size={16} />
          </button>

          <nav className="topbar-crumb">
            <a href="/dashboard">AutoSending</a>
            <ChevronRight size={13} className="sep" />
            <span className="cur">{crumb}</span>
          </nav>

          <div className="topbar-actions">
            <button className="btn-icon" title="Уведомления" style={{ position: "relative" }}>
              <Bell size={15} />
              <span
                style={{
                  position: "absolute",
                  top: 6,
                  right: 6,
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: "var(--coral)",
                  border: "1.5px solid var(--bg-1)",
                }}
              />
            </button>
            <button className="btn-icon" title="Помощь"><LifeBuoy size={15} /></button>
          </div>
        </header>

        <div style={{ padding: "32px var(--gutter) 56px", maxWidth: "var(--content-max)", margin: "0 auto", width: "100%" }}>
          {children}
        </div>
      </main>
    </div>
  );
}
