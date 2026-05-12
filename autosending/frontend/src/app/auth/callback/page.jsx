"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

// Receives ?token=JWT from the gramly.tech relay after Telegram auth.
// Stores it in localStorage and redirects to dashboard.
export default function AuthCallback() {
  const router = useRouter();

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get("token");
    if (token) {
      localStorage.setItem("token", token);
      router.replace("/dashboard/");
    } else {
      router.replace("/login/");
    }
  }, [router]);

  return null;
}
