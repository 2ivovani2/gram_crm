"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import api from "@/lib/api";

// Receives ?token=JWT from the gramly.tech relay after Telegram auth.
// Stores token, prefetches user data into cache, then redirects to dashboard.
export default function AuthCallback() {
  const router = useRouter();

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get("token");
    if (!token) { router.replace("/login/"); return; }

    localStorage.setItem("token", token);

    // Prefetch user so AuthContext initializes with user immediately (no sidebar flash).
    api.get("/api/auth/me")
      .then(r => {
        localStorage.setItem("user_cache", JSON.stringify(r.data));
        router.replace("/dashboard/");
      })
      .catch(() => {
        // Token invalid — clear and go to login.
        localStorage.removeItem("token");
        router.replace("/login/");
      });
  }, [router]);

  return null;
}
