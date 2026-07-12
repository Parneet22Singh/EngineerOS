"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  api,
  Finding,
  Scan,
  Severity,
  SEVERITY_META,
  SEVERITY_ORDER,
} from "@/lib/api";
import { useScanStream } from "@/lib/useScanStream";
import { CategoryTag, ScoreRing, SeverityBadge, StatCard, StatusPill } from "@/components/ui";

export default function ScanDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [scan, setScan] = useState<Scan | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [filter, setFilter] = useState<Severity | "all">("all");

  const load = useCallback(async () => {
    try {
      setScan(await api.getScan(id));
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : "Failed to load scan");
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  const active = scan ? scan.status === "queued" || scan.status === "running" : false;
  const { frame, terminal } = useScanStream(id, active);

  // Poll while active (covers the gap before the WS connects) and refetch on terminal.
  useEffect(() => {
    if (!active) return;
    const t = setInterval(load, 2500);
    return () => clearInterval(t);
  }, [active, load]);
  useEffect(() => {
    if (terminal) load();
  }, [terminal, load]);

  if (loadErr)
    return (
      <div className="card p-10 text-center">
        <p className="text-red-400">{loadErr}</p>
        <Link href="/" className="btn-ghost mt-4">← Back</Link>
      </div>
    );
  if (!scan) return <SkeletonReport />;

  const summary = scan.summary as any;
  const findings = scan.findings ?? [];
  const running = scan.status === "queued" || scan.status === "running";
  const progressPct = Math.round((frame?.progress ?? scan.progress) * 100);

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href="/" className="text-xs text-muted hover:text-white">← All scans</Link>
          <h1 className="mt-1 break-all text-2xl font-bold">{scan.target}</h1>
          <div className="mt-2 flex items-center gap-3 text-xs text-muted">
            <StatusPill status={scan.status} />
            <span className="rounded-full border border-border px-2 py-0.5">
              {scan.module === "autonomous_qa" ? "🤖 Autonomous QA" : "🌐 Website Intelligence"}
            </span>
            <span className="font-mono">{scan.id.slice(0, 8)}</span>
          </div>
        </div>
        {scan.status === "completed" && (
          <div className="flex flex-wrap gap-2">
            <a className="btn-ghost" href={api.reportUrl(scan.id, "html")} target="_blank" rel="noreferrer">HTML</a>
            <a className="btn-ghost" href={api.reportUrl(scan.id, "pdf")} target="_blank" rel="noreferrer">PDF</a>
            <a className="btn-ghost" href={api.reportUrl(scan.id, "json")} target="_blank" rel="noreferrer">JSON</a>
            <a className="btn-ghost" href={api.reportUrl(scan.id, "csv")} target="_blank" rel="noreferrer">CSV</a>
          </div>
        )}
      </div>

      {/* Live progress */}
      {running && (
        <div className="card p-6">
          <div className="mb-2 flex items-center justify-between text-sm">
            <span className="font-medium capitalize">{(frame?.stage ?? scan.stage).replace(/[:_]/g, " ")}</span>
            <span className="text-accent">{progressPct}%</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-panel2">
            <div
              className="h-full rounded-full bg-gradient-to-r from-accent to-fuchsia-500 transition-all duration-500"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          {frame?.detail && <p className="mt-2 truncate text-xs text-muted">{frame.detail}</p>}
        </div>
      )}

      {scan.status === "failed" && (
        <div className="card border-red-500/40 bg-red-500/5 p-6">
          <p className="font-medium text-red-400">Scan failed</p>
          <p className="mt-1 text-sm text-muted">{scan.error}</p>
        </div>
      )}

      {scan.status === "completed" && summary && (
        <>
          {/* Overview */}
          <section className="grid gap-4 md:grid-cols-[auto_1fr]">
            <div className="card flex items-center justify-center p-6">
              <ScoreRing score={summary.health_score ?? 0} />
            </div>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              <StatCard label="Findings" value={summary.total_findings} />
              {summary.actions_performed != null ? (
                <StatCard label="Interactions" value={summary.actions_performed} />
              ) : (
                <StatCard label="Pages scanned" value={summary.pages_scanned} />
              )}
              <StatCard
                label="Critical + High"
                value={(summary.by_severity?.critical ?? 0) + (summary.by_severity?.high ?? 0)}
                accent
              />
              {summary.lighthouse && !summary.lighthouse.skipped
                ? Object.entries(summary.lighthouse)
                    .filter(([, v]) => typeof v === "number")
                    .slice(0, 3)
                    .map(([k, v]) => (
                      <StatCard key={k} label={`LH ${k.replace("-", " ")}`} value={v as number} />
                    ))
                : null}
            </div>
          </section>

          {/* Severity bar */}
          <SeverityBar bySeverity={summary.by_severity} onPick={setFilter} active={filter} />

          {/* Findings */}
          <section>
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold">
                Findings{" "}
                <span className="text-muted">
                  ({filter === "all" ? findings.length : findings.filter((f) => f.severity === filter).length})
                </span>
              </h2>
              {filter !== "all" && (
                <button className="text-xs text-muted hover:text-white" onClick={() => setFilter("all")}>
                  Clear filter
                </button>
              )}
            </div>
            <FindingsList findings={findings} filter={filter} />
          </section>

          {/* Explored flows (autonomous QA) */}
          <FlowsTable flows={summary.flows ?? []} />

          {/* Pages */}
          <PagesTable pages={summary.pages ?? []} />

          {/* Screenshots */}
          <Screenshots pages={summary.pages ?? []} />
        </>
      )}
    </div>
  );
}

function SeverityBar({
  bySeverity,
  onPick,
  active,
}: {
  bySeverity: Record<Severity, number>;
  onPick: (s: Severity | "all") => void;
  active: Severity | "all";
}) {
  const total = SEVERITY_ORDER.reduce((a, s) => a + (bySeverity?.[s] ?? 0), 0) || 1;
  return (
    <section className="card p-5">
      <div className="mb-3 flex h-3 overflow-hidden rounded-full border border-border">
        {SEVERITY_ORDER.map((s) =>
          bySeverity?.[s] ? (
            <div
              key={s}
              style={{ flex: bySeverity[s], background: SEVERITY_META[s].color }}
              title={`${SEVERITY_META[s].label}: ${bySeverity[s]}`}
            />
          ) : null
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        {SEVERITY_ORDER.map((s) => (
          <button
            key={s}
            onClick={() => onPick(active === s ? "all" : s)}
            className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs transition ${
              active === s ? "border-accent bg-accent/10" : "border-border hover:border-slate-500"
            }`}
          >
            <span className="h-2.5 w-2.5 rounded-sm" style={{ background: SEVERITY_META[s].color }} />
            {SEVERITY_META[s].label}
            <span className="font-semibold">{bySeverity?.[s] ?? 0}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

function FindingsList({ findings, filter }: { findings: Finding[]; filter: Severity | "all" }) {
  const shown = useMemo(
    () => (filter === "all" ? findings : findings.filter((f) => f.severity === filter)),
    [findings, filter]
  );
  if (shown.length === 0)
    return <div className="card p-10 text-center text-muted">No findings in this category. 🎉</div>;
  return (
    <div className="space-y-3">
      {shown.map((f) => (
        <div
          key={f.id}
          className="card animate-fade-up p-5"
          style={{ borderLeft: `3px solid ${SEVERITY_META[f.severity].color}` }}
        >
          <div className="flex items-center gap-2">
            <SeverityBadge severity={f.severity} />
            <CategoryTag category={f.category} />
          </div>
          <h3 className="mt-2.5 font-semibold">{f.title}</h3>
          {f.description && <p className="mt-1 text-sm text-slate-300">{f.description}</p>}
          {f.recommendation && (
            <p className="mt-1.5 text-sm text-emerald-300">↳ {f.recommendation}</p>
          )}
          {f.page_url && <p className="mt-2 break-all text-xs text-muted">{f.page_url}</p>}
          {f.element && (
            <p className="mt-1 break-all text-xs text-muted">
              Element: <code className="rounded bg-panel2 px-1.5 py-0.5">{f.element}</code>
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

function FlowsTable({ flows }: { flows: any[] }) {
  if (!flows.length) return null;
  return (
    <section>
      <h2 className="mb-4 text-lg font-semibold">
        Explored flows <span className="text-muted">({flows.length})</span>
      </h2>
      <div className="overflow-x-auto rounded-2xl border border-border">
        <table className="w-full text-sm">
          <thead className="bg-panel2 text-left text-muted">
            <tr>
              <th className="px-4 py-3 font-medium">Action</th>
              <th className="px-4 py-3 font-medium">Target</th>
              <th className="px-4 py-3 font-medium">Result</th>
            </tr>
          </thead>
          <tbody>
            {flows.map((f, i) => (
              <tr key={i} className="border-t border-border">
                <td className="px-4 py-3 capitalize text-muted">{f.action}</td>
                <td className="px-4 py-3">{f.label}</td>
                <td className={`px-4 py-3 ${f.issue ? "text-high" : "text-emerald-400"}`}>
                  {f.issue ? "⚠ " : "✓ "}
                  {f.result}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function PagesTable({ pages }: { pages: any[] }) {
  if (!pages.length) return null;
  return (
    <section>
      <h2 className="mb-4 text-lg font-semibold">Pages</h2>
      <div className="overflow-x-auto rounded-2xl border border-border">
        <table className="w-full text-sm">
          <thead className="bg-panel2 text-left text-muted">
            <tr>
              <th className="px-4 py-3 font-medium">URL</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 font-medium">Load</th>
              <th className="px-4 py-3 font-medium">Findings</th>
            </tr>
          </thead>
          <tbody>
            {pages.map((p, i) => (
              <tr key={i} className="border-t border-border">
                <td className="px-4 py-3">
                  <div className="break-all">{p.url}</div>
                  {p.title && <div className="text-xs text-muted">{p.title}</div>}
                </td>
                <td className={`px-4 py-3 ${p.status_code && p.status_code < 400 ? "text-emerald-400" : "text-high"}`}>
                  {p.status_code ?? p.error ?? "—"}
                </td>
                <td className="px-4 py-3 text-muted">{p.load_ms} ms</td>
                <td className="px-4 py-3 text-muted">{p.findings}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Screenshots({ pages }: { pages: any[] }) {
  const shots = pages.flatMap((p) =>
    (p.screenshots ?? []).map((s: any) => ({ ...s, page: p.url }))
  );
  if (!shots.length) return null;
  return (
    <section>
      <h2 className="mb-4 text-lg font-semibold">Screenshots</h2>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
        {shots.slice(0, 12).map((s: any, i: number) => (
          <a
            key={i}
            href={api.artifactUrl(s.path)}
            target="_blank"
            rel="noreferrer"
            className="card group overflow-hidden"
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={api.artifactUrl(s.path)}
              alt={`${s.viewport} · ${s.page}`}
              className="h-40 w-full object-cover object-top transition group-hover:opacity-90"
            />
            <div className="flex items-center justify-between px-3 py-2 text-[11px] text-muted">
              <span className="uppercase">{s.viewport}</span>
              <span className="truncate">{new URL(s.page).pathname}</span>
            </div>
          </a>
        ))}
      </div>
    </section>
  );
}

function SkeletonReport() {
  return (
    <div className="space-y-6">
      <div className="h-8 w-1/3 rounded-lg bg-panel shimmer" />
      <div className="grid gap-4 md:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-28 rounded-2xl bg-panel shimmer" />
        ))}
      </div>
      <div className="h-64 rounded-2xl bg-panel shimmer" />
    </div>
  );
}
