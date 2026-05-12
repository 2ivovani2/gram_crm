"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";

const GRAMLY_URL = process.env.NEXT_PUBLIC_GRAMLY_URL || "https://gramly.tech";

export default function LoginPage() {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && user) {
      router.replace("/dashboard/");
    } else if (!loading && !user) {
      // nginx redirects /login/ → gramly.tech/spam-login/ in prod,
      // this fallback handles direct access in dev
      window.location.href = `${GRAMLY_URL}/spam-login/`;
    }
  }, [user, loading, router]);

  return null;
}
