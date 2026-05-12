"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import api from "@/lib/api";
import { authWithTelegram } from "@/lib/api";

const AuthContext = createContext(null);

// Pages that don't require auth — / redirects to /login or /dashboard itself
const PUBLIC_PATHS = ["/", "/login", "/login/"];

export function AuthProvider({ children }) {
  const [user,    setUser]    = useState(() => {
    // Hydrate from cache immediately — zero-delay render on page reload
    if (typeof window === "undefined") return null;
    try { return JSON.parse(localStorage.getItem("user_cache") || "null"); } catch { return null; }
  });
  const [loading, setLoading] = useState(true);
  const router   = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) {
      localStorage.removeItem("user_cache");
      setUser(null);
      setLoading(false);
      if (!PUBLIC_PATHS.includes(pathname)) router.replace("/login/");
      return;
    }

    // Optimistic: already set user from cache above, mark loaded immediately.
    setLoading(false);

    // Verify token in background; update user data or redirect on failure.
    api.get("/api/auth/me")
      .then(r => {
        setUser(r.data);
        localStorage.setItem("user_cache", JSON.stringify(r.data));
      })
      .catch(() => {
        localStorage.removeItem("token");
        localStorage.removeItem("user_cache");
        setUser(null);
        router.replace("/login/");
      });
  }, []);

  const login = (token, userData) => {
    localStorage.setItem("token", token);
    localStorage.setItem("user_cache", JSON.stringify(userData));
    setUser(userData);
    router.replace("/dashboard/");
  };

  const loginWithTelegram = async (tgData) => {
    const res = await authWithTelegram(tgData);
    login(res.access_token, res.user);
  };

  const logout = () => {
    localStorage.removeItem("token");
    setUser(null);
    router.replace("/login/");
  };

  if (loading) {
    return (
      <div style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--bg)",
      }}>
        <div className="spinner" />
      </div>
    );
  }

  return (
    <AuthContext.Provider value={{ user, login, loginWithTelegram, logout, loading }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
};
