"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";

export default function LoginPage() {
  const { user, loading } = useAuth();
  const router = useRouter();

  // Already authenticated → skip to dashboard
  useEffect(() => {
    if (!loading && user) router.replace("/dashboard/");
  }, [user, loading, router]);

  // No widget mounted here — login happens via gramly.tech relay
  // (Telegram Login Widget requires the page to be on the bot's registered domain)
  useEffect(() => {}, [loading, user]);

  if (loading) return null;
  if (user) return null;

  return (
    <div style={{
      minHeight: "100vh",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      background: "var(--bg)",
      padding: "24px",
    }}>
      <div style={{
        width: "100%",
        maxWidth: 360,
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: 12,
        padding: "36px 32px",
        textAlign: "center",
      }}>
        {/* Gramly logotype */}
        <div style={{ marginBottom: 24 }}>
          <div style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 8,
            marginBottom: 8,
          }}>
            <div style={{
              width: 32, height: 32,
              background: "var(--p)",
              borderRadius: 8,
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: 16,
            }}>
              💛
            </div>
            <span style={{
              fontSize: 20, fontWeight: 700,
              letterSpacing: "-0.04em",
              color: "var(--text-1)",
            }}>
              Gramly
            </span>
          </div>
          <div style={{ fontSize: 13, color: "var(--text-4)" }}>
            AutoSending
          </div>
        </div>

        <h1 style={{
          fontSize: 18, fontWeight: 600,
          letterSpacing: "-0.02em",
          color: "var(--text-1)",
          marginBottom: 8,
        }}>
          Войти в систему
        </h1>
        <p style={{
          fontSize: 13, color: "var(--text-4)",
          lineHeight: 1.5, marginBottom: 28,
        }}>
          Используй свой Telegram-аккаунт
        </p>

        {/* Login via gramly.tech relay (Telegram widget requires bot's domain) */}
        <a
          href={`${process.env.NEXT_PUBLIC_GRAMLY_URL || "https://gramly.tech"}/spam-login/`}
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            width: "100%",
            padding: "10px 16px",
            background: "linear-gradient(135deg, #f0b429 0%, #e07d0a 100%)",
            color: "#0b0a05",
            fontWeight: 600,
            fontSize: 14,
            borderRadius: 8,
            textDecoration: "none",
            marginBottom: 16,
          }}
        >
          Войти через Telegram
        </a>

        <p style={{
          marginTop: 28,
          fontSize: 11,
          color: "var(--text-4)",
          lineHeight: 1.5,
        }}>
          Часть экосистемы{" "}
          <a href={`${process.env.NEXT_PUBLIC_GRAMLY_URL || ""}/`} style={{ color: "var(--text-3)", textDecoration: "none" }}>Gramly</a>
          {" · "}
          <a href={`${process.env.NEXT_PUBLIC_GRAMLY_URL || ""}/crm/login/`} style={{ color: "var(--text-3)", textDecoration: "none" }}>CRM</a>
        </p>
      </div>
    </div>
  );
}
