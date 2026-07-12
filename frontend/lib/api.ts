// Typed client for the EngineerOS backend API.

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") || "http://localhost:8000";

export type Severity = "critical" | "high" | "medium" | "low" | "info";
export type ScanStatus = "queued" | "running" | "completed" | "failed";

export interface Finding {
  id: number;
  category: string;
  severity: Severity;
  title: string;
  description: string;
  recommendation: string;
  page_url: string;
  element: string | null;
  evidence: Record<string, unknown>;
  priority: number;
}

export interface PageSummary {
  url: string;
  final_url: string | null;
  status_code: number | null;
  title: string;
  depth: number;
  load_ms: number;
  findings: number;
  error: string | null;
  screenshots: { viewport: string; path: string; width: number; height: number }[];
}

export interface ScanSummary {
  target: string;
  origin: string;
  pages_scanned: number;
  total_findings: number;
  by_severity: Record<Severity, number>;
  by_category: Record<string, number>;
  lighthouse: Record<string, number | boolean | string>;
  health_score: number;
  pages: PageSummary[];
}

export interface Scan {
  id: string;
  module: string;
  target: string;
  status: ScanStatus;
  progress: number;
  stage: string;
  pages_scanned: number;
  summary: ScanSummary | Record<string, never>;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  findings?: Finding[];
}

export interface ModuleInfo {
  name: string;
  title: string;
  version: string;
  description: string;
  capabilities: string[];
}

export interface ScanOptions {
  max_pages?: number;
  max_depth?: number;
  respect_robots?: boolean;
  run_lighthouse?: boolean;
  check_external_links?: boolean;
  max_actions?: number;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${text ? `: ${text}` : ""}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => fetch(`${API_BASE}/api/health`).then(json<{ status: string; modules: string[] }>),
  modules: () => fetch(`${API_BASE}/api/modules`, { cache: "no-store" }).then(json<ModuleInfo[]>),
  listScans: () => fetch(`${API_BASE}/api/scans`, { cache: "no-store" }).then(json<Scan[]>),
  getScan: (id: string) =>
    fetch(`${API_BASE}/api/scans/${id}`, { cache: "no-store" }).then(json<Scan>),
  createScan: (url: string, module: string, options: ScanOptions) =>
    fetch(`${API_BASE}/api/scans`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url, module, options }),
    }).then(json<Scan>),
  reportUrl: (id: string, fmt: "json" | "csv" | "html" | "pdf") =>
    `${API_BASE}/api/scans/${id}/report.${fmt}`,
  streamUrl: (id: string) =>
    `${API_BASE.replace(/^http/, "ws")}/api/scans/${id}/stream`,
  artifactUrl: (path: string) => `${API_BASE}/artifacts/${path}`,
};

export const SEVERITY_META: Record<Severity, { label: string; color: string }> = {
  critical: { label: "Critical", color: "#ef4444" },
  high: { label: "High", color: "#f97316" },
  medium: { label: "Medium", color: "#eab308" },
  low: { label: "Low", color: "#38bdf8" },
  info: { label: "Info", color: "#94a3b8" },
};

export const SEVERITY_ORDER: Severity[] = ["critical", "high", "medium", "low", "info"];
