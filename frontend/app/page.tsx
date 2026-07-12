"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, Scan, ModuleInfo, ScanOptions } from "@/lib/api";
import { StatusPill } from "@/components/ui";

const MODULES = [
  {
    name: "website_intelligence",
    title: "Website Intelligence",
    tagline: "Crawl a whole site — a11y, SEO, links, Lighthouse.",
    icon: "🌐",
  },
  {
    name: "autonomous_qa",
    title: "Autonomous QA Agent",
    tagline: "Explore one page — click, open menus, fill & submit forms.",
    icon: "🤖",
  },
];

export default function HomePage() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [module, setModule] = useState("website_intelligence");
  const [opts, setOpts] = useState<ScanOptions>({
    max_pages: 15,
    max_depth: 2,
    max_actions: 18,
    run_lighthouse: false,
    respect_robots: true,
    check_external_links: true,
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scans, setScans] = useState<Scan[]>([]);
  const [modules, setModules] = useState<ModuleInfo[]>([]);
  const [online, setOnline] = useState<boolean | null>(null);

  const refresh = async () => {
    try {
      const [s, m] = await Promise.all([api.listScans(), api.modules()]);
      setScans(s);
      setModules(m);
      setOnline(true);
    } catch {
      setOnline(false);
    }
  };

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 4000);
    return () => clearInterval(t);
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    let target = url.trim();
    if (!target) return;
    if (!/^https?:\/\//i.test(target)) target = "https://" + target;
    setSubmitting(true);
    try {
      const scan = await api.createScan(target, module, opts);
      router.push(`/scans/${scan.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start scan");
      setSubmitting(false);
    }
  };

  const mod = modules[0];

  return (
    <div className="space-y-12">
      {/* Hero + launcher */}
      <section className="animate-fade-up">
        <div className="mb-8 max-w-2xl">
          <h1 className="text-4xl font-bold tracking-tight">
            Audit any website in one command.
          </h1>
          <p className="mt-3 text-muted">
            EngineerOS crawls a site, runs accessibility, SEO, responsive, broken-link,
            console and network checks, captures multi-device screenshots, and produces an
            enterprise QA report — HTML, PDF, JSON and CSV.
          </p>
        </div>

        {/* Module selector */}
        <div className="mb-4 grid gap-3 sm:grid-cols-2">
          {MODULES.map((m) => (
            <button
              key={m.name}
              type="button"
              onClick={() => setModule(m.name)}
              className={`card flex items-start gap-3 p-4 text-left transition ${
                module === m.name ? "border-accent ring-1 ring-accent/40" : "hover:border-slate-500"
              }`}
            >
              <span className="text-2xl leading-none">{m.icon}</span>
              <span>
                <span className="block text-sm font-semibold">{m.title}</span>
                <span className="mt-0.5 block text-xs text-muted">{m.tagline}</span>
              </span>
            </button>
          ))}
        </div>

        <form onSubmit={submit} className="card p-6">
          <div className="flex flex-col gap-3 sm:flex-row">
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com"
              className="flex-1 rounded-xl border border-border bg-panel2 px-4 py-3 text-sm outline-none placeholder:text-slate-500 focus:border-accent"
              autoFocus
            />
            <button type="submit" disabled={submitting || !url.trim()} className="btn-primary px-6 py-3">
              {submitting ? "Starting…" : "Run scan"}
            </button>
          </div>

          {module === "website_intelligence" ? (
            <div className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-4">
              <NumberField
                label="Max pages"
                value={opts.max_pages ?? 15}
                min={1}
                max={200}
                onChange={(v) => setOpts({ ...opts, max_pages: v })}
              />
              <NumberField
                label="Max depth"
                value={opts.max_depth ?? 2}
                min={0}
                max={6}
                onChange={(v) => setOpts({ ...opts, max_depth: v })}
              />
              <Toggle
                label="Lighthouse"
                checked={!!opts.run_lighthouse}
                onChange={(v) => setOpts({ ...opts, run_lighthouse: v })}
              />
              <Toggle
                label="Respect robots"
                checked={!!opts.respect_robots}
                onChange={(v) => setOpts({ ...opts, respect_robots: v })}
              />
            </div>
          ) : (
            <div className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-4">
              <NumberField
                label="Max actions"
                value={opts.max_actions ?? 18}
                min={1}
                max={60}
                onChange={(v) => setOpts({ ...opts, max_actions: v })}
              />
              <Toggle
                label="Lighthouse"
                checked={!!opts.run_lighthouse}
                onChange={(v) => setOpts({ ...opts, run_lighthouse: v })}
              />
            </div>
          )}
          {error && <p className="mt-4 text-sm text-red-400">{error}</p>}
        </form>

        <div className="mt-3 flex items-center gap-2 text-xs text-muted">
          <span
            className={`h-2 w-2 rounded-full ${
              online == null ? "bg-slate-500" : online ? "bg-emerald-400" : "bg-red-400"
            }`}
          />
          {online == null
            ? "Connecting to backend…"
            : online
            ? `Backend online${mod ? ` · ${mod.title} v${mod.version}` : ""}`
            : "Backend offline — start it at http://localhost:8000"}
        </div>
      </section>

      {/* Recent scans */}
      <section>
        <h2 className="mb-4 text-lg font-semibold">Recent scans</h2>
        {scans.length === 0 ? (
          <div className="card p-10 text-center text-muted">No scans yet. Run your first one above.</div>
        ) : (
          <div className="overflow-hidden rounded-2xl border border-border">
            <table className="w-full text-sm">
              <thead className="bg-panel2 text-left text-muted">
                <tr>
                  <th className="px-4 py-3 font-medium">Target</th>
                  <th className="px-4 py-3 font-medium">Module</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">Findings</th>
                  <th className="px-4 py-3 font-medium">Health</th>
                </tr>
              </thead>
              <tbody>
                {scans.map((s) => {
                  const sum = s.summary as any;
                  return (
                    <tr key={s.id} className="border-t border-border transition hover:bg-panel2/50">
                      <td className="px-4 py-3">
                        <Link href={`/scans/${s.id}`} className="font-medium hover:text-accent">
                          {s.target}
                        </Link>
                      </td>
                      <td className="px-4 py-3">
                        <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted">
                          {s.module === "autonomous_qa" ? "🤖 QA" : "🌐 Web"}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        {s.status === "running" ? (
                          <span className="text-accent">{Math.round(s.progress * 100)}%</span>
                        ) : (
                          <StatusPill status={s.status} />
                        )}
                      </td>
                      <td className="px-4 py-3 text-muted">{sum?.total_findings ?? "—"}</td>
                      <td className="px-4 py-3">
                        {sum?.health_score != null ? (
                          <span className="font-semibold">{sum.health_score}</span>
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="label">{label}</span>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        onChange={(e) => onChange(Math.max(min, Math.min(max, Number(e.target.value) || min)))}
        className="rounded-lg border border-border bg-panel2 px-3 py-2 text-sm outline-none focus:border-accent"
      />
    </label>
  );
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer flex-col gap-1">
      <span className="label">{label}</span>
      <button
        type="button"
        onClick={() => onChange(!checked)}
        className={`flex h-9 items-center rounded-lg border px-1 transition ${
          checked ? "border-accent/50 bg-accent/20" : "border-border bg-panel2"
        }`}
      >
        <span
          className={`h-6 w-6 rounded-md transition ${
            checked ? "translate-x-[calc(100%-0.25rem)] bg-accent" : "bg-slate-600"
          }`}
        />
      </button>
    </label>
  );
}
