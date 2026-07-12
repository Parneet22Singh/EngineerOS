import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0b0f19",
        panel: "#121826",
        panel2: "#171f30",
        border: "#232c40",
        muted: "#94a3b8",
        accent: "#6366f1",
        critical: "#ef4444",
        high: "#f97316",
        medium: "#eab308",
        low: "#38bdf8",
        info: "#94a3b8",
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "Segoe UI", "Roboto", "Helvetica", "Arial"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      keyframes: {
        shimmer: { "100%": { transform: "translateX(100%)" } },
        "fade-up": { "0%": { opacity: "0", transform: "translateY(8px)" }, "100%": { opacity: "1", transform: "translateY(0)" } },
      },
      animation: {
        "fade-up": "fade-up .4s ease both",
      },
    },
  },
  plugins: [],
};

export default config;
