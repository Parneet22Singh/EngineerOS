import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "EngineerOS — Website Intelligence",
  description: "The AI operating system for software engineers.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">
        <header className="sticky top-0 z-20 border-b border-border bg-bg/70 backdrop-blur">
          <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-3.5">
            <Link href="/" className="flex items-center gap-2.5">
              <span className="h-6 w-6 rounded-lg bg-gradient-to-br from-accent to-fuchsia-500 shadow-lg shadow-accent/30" />
              <span className="text-sm font-semibold tracking-wide">
                Engineer<span className="text-accent">OS</span>
              </span>
              <span className="ml-1 rounded-full border border-border px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted">
                Website Intelligence
              </span>
            </Link>
            <nav className="flex items-center gap-5 text-sm text-muted">
              <Link href="/" className="hover:text-white transition">
                Scans
              </Link>
              <a
                href="http://localhost:8000/docs"
                target="_blank"
                rel="noreferrer"
                className="hover:text-white transition"
              >
                API
              </a>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-6 py-10">{children}</main>
      </body>
    </html>
  );
}
