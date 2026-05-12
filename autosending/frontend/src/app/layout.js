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
      <body style={{ background: "var(--bg)", color: "var(--text-1)", minHeight: "100vh" }}>
        <AuthProvider>
          <AppShell>{children}</AppShell>
        </AuthProvider>

        <Toaster
          position="bottom-right"
          gutter={8}
          toastOptions={{
            duration: 3500,
            style: {
              background: "var(--surface)",
              color:       "var(--text-1)",
              border:      "1px solid var(--border-2)",
              borderRadius:"var(--r)",
              fontSize:    "13px",
              padding:     "10px 14px",
              boxShadow:   "0 8px 24px oklch(0.04 0.002 255 / 0.6)",
            },
            success: { iconTheme: { primary: "var(--green)",  secondary: "var(--green-bg)" } },
            error:   { iconTheme: { primary: "var(--red)",    secondary: "var(--red-bg)" } },
          }}
        />
      </body>
    </html>
  );
}
