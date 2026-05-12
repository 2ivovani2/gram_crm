/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/pages/**/*.{js,jsx}",
    "./src/components/**/*.{js,jsx}",
    "./src/app/**/*.{js,jsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg:      "#020617",
        surface: "#080f1f",
        card:    "#0b1526",
        "card-hover": "#0f1b30",
        "border-dim": "rgba(59,130,246,0.1)",
        "border-mid": "rgba(59,130,246,0.2)",
        "border-hi":  "rgba(59,130,246,0.4)",
        txt:     "#e2e8f0",
        muted:   "#64748b",
        subtle:  "#334155",
        blue:    { DEFAULT: "#3b82f6", l: "#60a5fa", d: "#1d4ed8", xl: "#93c5fd" },
        em:      { DEFAULT: "#10b981", l: "#34d399", d: "#059669" },
        am:      { DEFAULT: "#f59e0b", l: "#fbbf24", d: "#d97706" },
        re:      { DEFAULT: "#ef4444", l: "#f87171", d: "#dc2626" },
        vi:      { DEFAULT: "#8b5cf6", l: "#a78bfa", d: "#7c3aed" },
        cy:      { DEFAULT: "#06b6d4", l: "#22d3ee", d: "#0891b2" },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
      },
      borderRadius: {
        "2xl": "16px",
        "3xl": "24px",
      },
      boxShadow: {
        glass: "0 4px 24px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.04)",
        "blue-glow": "0 0 20px rgba(59,130,246,0.25)",
        "blue-glow-sm": "0 0 10px rgba(59,130,246,0.15)",
      },
      animation: {
        "pulse-slow":  "pulse 3s ease-in-out infinite",
        "fade-in":     "fadeIn 0.3s ease-out",
        "slide-up":    "slideUp 0.3s ease-out",
        "ping-slow":   "ping 2.5s ease-in-out infinite",
      },
      keyframes: {
        fadeIn:  { from: { opacity: 0, transform: "translateY(6px)" }, to: { opacity: 1, transform: "none" } },
        slideUp: { from: { opacity: 0, transform: "translateY(16px)" }, to: { opacity: 1, transform: "none" } },
      },
      backdropBlur: { xs: "4px" },
    },
  },
  plugins: [],
};
