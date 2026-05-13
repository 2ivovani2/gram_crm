import "./globals.css";
import { Toaster } from "react-hot-toast";
import { AuthProvider } from "@/contexts/AuthContext";
import AppShell from "@/components/AppShell";

export const metadata = {
  title: "AutoSending — Telegram-автоматизация",
  description: "Управляй аккаунтами, каналами и кампаниями из единого дашборда.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="ru">
      <body style={{ background: "var(--bg)", color: "var(--ink-1)", minHeight: "100vh" }}>
        <AuthProvider>
          <AppShell>{children}</AppShell>
        </AuthProvider>

        <Toaster
          position="bottom-right"
          gutter={10}
          toastOptions={{
            duration: 3500,
            style: {
              background: "linear-gradient(180deg, #15161a, #0e0f12)",
              color:        "var(--ink-1)",
              border:       "1px solid var(--line-3)",
              borderRadius: "10px",
              fontSize:     "13px",
              padding:      "11px 14px",
              boxShadow:    "var(--shadow-3)",
              fontFamily:   "var(--sans)",
            },
            success: { iconTheme: { primary: "var(--emerald)", secondary: "var(--emerald-tint)" } },
            error:   { iconTheme: { primary: "var(--coral)",   secondary: "var(--coral-tint)" } },
          }}
        />
      </body>
    </html>
  );
}
