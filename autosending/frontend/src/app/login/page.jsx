"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";

export default function LoginPage() {
  const { user, loading, loginWithTelegram } = useAuth();
  const router    = useRouter();
  const widgetRef = useRef(null);
  const [error, setError]   = useState("");
  const [busy,  setBusy]    = useState(false);

  // Already authenticated → skip to dashboard
  useEffect(() => {
    if (!loading && user) router.replace("/dashboard/");
  }, [user, loading, router]);

  // Mount Telegram Login Widget once the page is ready
  useEffect(() => {
    if (loading || user) return;

    // Global callback: Telegram Widget calls window.onTelegramAuth(data)
    window.onTelegramAuth = async (tgData) => {
      setBusy(true);
      setError("");
      try {
        await loginWithTelegram(tgData);
      } catch (err) {
        setError(
          err?.response?.data?.detail ||
          "Ошибка авторизации. Попробуйте ещё раз."
        );
      } finally {
        setBusy(false);
      }
    };

    const script = document.createElement("script");
    script.src   = "https://telegram.org/js/telegram-widget.js?22";
    script.setAttribute("data-telegram-login", process.env.NEXT_PUBLIC_BOT_USERNAME || "gramlycrmtestbot");
    script.setAttribute("data-size",           "large");
    script.setAttribute("data-onauth",         "onTelegramAuth(user)");
    script.setAttribute("data-request-access", "write");
    script.async = true;
    widgetRef.current?.appendChild(script);

    return () => {
      delete window.onTelegramAuth;
    };
  }, [loading, user]);

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

        {/* Telegram Widget mount point */}
        <div
          ref={widgetRef}
          style={{
            display: "flex",
            justifyContent: "center",
            marginBottom: error ? 16 : 0,
            minHeight: 44,
            opacity: busy ? 0.5 : 1,
            pointerEvents: busy ? "none" : "auto",
            transition: "opacity 150ms",
          }}
        />

        {busy && (
          <div style={{
            display: "flex", justifyContent: "center",
            alignItems: "center", gap: 8,
            fontSize: 13, color: "var(--text-3)",
            marginBottom: 12,
          }}>
            <span className="spinner" style={{ width: 14, height: 14 }} />
            Авторизация…
          </div>
        )}

        {error && (
          <div style={{
            fontSize: 12,
            color: "var(--red)",
            background: "var(--red-bg)",
            border: "1px solid var(--red-b)",
            borderRadius: 6,
            padding: "8px 12px",
            textAlign: "left",
          }}>
            {error}
          </div>
        )}

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
