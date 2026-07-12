import { SEVERITY_META, Severity } from "@/lib/api";

export function SeverityBadge({ severity }: { severity: Severity }) {
  const m = SEVERITY_META[severity];
  return (
    <span
      className="rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-bg"
      style={{ background: m.color }}
    >
      {m.label}
    </span>
  );
}

export function CategoryTag({ category }: { category: string }) {
  return (
    <span className="rounded-full border border-border px-2 py-0.5 text-[10px] uppercase tracking-wide text-muted">
      {category}
    </span>
  );
}

/** Circular score gauge (0–100). */
export function ScoreRing({ score, size = 116 }: { score: number; size?: number }) {
  const r = size / 2 - 8;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, score));
  const dash = (pct / 100) * c;
  const color = pct >= 90 ? "#4ade80" : pct >= 70 ? "#eab308" : pct >= 40 ? "#f97316" : "#ef4444";
  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} stroke="#232c40" strokeWidth={8} fill="none" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          stroke={color}
          strokeWidth={8}
          fill="none"
          strokeLinecap="round"
          strokeDasharray={`${dash} ${c}`}
          style={{ transition: "stroke-dasharray .8s ease" }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-3xl font-bold" style={{ color }}>
          {pct}
        </span>
        <span className="label">Health</span>
      </div>
    </div>
  );
}

export function StatCard({ label, value, accent }: { label: string; value: React.ReactNode; accent?: boolean }) {
  return (
    <div className="card p-5">
      <div className="label">{label}</div>
      <div className={`mt-1 text-3xl font-bold ${accent ? "text-accent" : ""}`}>{value}</div>
    </div>
  );
}

export function StatusPill({ status }: { status: string }) {
  const map: Record<string, string> = {
    completed: "text-emerald-400 border-emerald-500/40 bg-emerald-500/10",
    running: "text-accent border-accent/40 bg-accent/10",
    queued: "text-slate-300 border-border bg-panel2",
    failed: "text-red-400 border-red-500/40 bg-red-500/10",
  };
  return (
    <span className={`rounded-full border px-2.5 py-0.5 text-[11px] font-medium capitalize ${map[status] || map.queued}`}>
      {status}
    </span>
  );
}
