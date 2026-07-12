# EngineerOS

> The AI Operating System for Software Engineers.

EngineerOS is a modular, plugin-based platform that analyzes websites, repositories,
APIs, documentation, and infrastructure, and unifies everything into a shared
intelligence layer.

This repository ships the **core platform** plus all six modules:

- **Module 1 — Universal Website Intelligence**: crawls a whole site (robots/sitemap-aware
  BFS) and audits accessibility, SEO, responsiveness, broken links, console/network errors,
  and Lighthouse.
- **Module 2 — Universal Repository Intelligence**: analyzes a local repo or GitHub URL —
  language/stack inventory, dependency manifests (npm/pip/go/maven/cargo/…), entry points,
  architecture structure map, internal import graph with circular-dependency detection,
  dead-code candidates, oversized files/functions, hardcoded-secret detection, TODO debt.
- **Module 3 — Universal API Intelligence**: discovers APIs two ways — live network capture
  (loads a URL, records XHR/fetch, detects REST + GraphQL, auth style, status codes) or static
  route extraction from a repo/GitHub URL (FastAPI, Flask, Express/Koa, NestJS, Spring, Go,
  Next.js file routes). Flags broken/insecure/duplicate endpoints and generates an OpenAPI 3.0
  spec + a Postman collection you can import directly.
- **Module 4 — Knowledge Graph**: a semantic map of a repo (`eos scan <repo> -m kg`) — builds a
  component graph from the import relationships, ranks components by connectivity (fan-in/fan-out),
  detects cycles, and uses the local (or cloud) AI to add a one-line role summary to the most
  important components. Exports `graph.json`. Degrades to a structure-only graph if AI is off.
- **Module 5 — AI Copilot**: a grounded, conversational coding assistant (`eos ask` / `eos chat`).
  Retrieves the most relevant source files for your question, feeds them to a local (or cloud)
  model, and answers with citations to the actual files. Provider-agnostic — runs against a
  local `llama.cpp` server or a cloud API key.
- **Module 6 — Autonomous QA Agent**: given only a URL, autonomously explores a single page —
  opens menus, clicks buttons, fills & submits forms, handles dialogs, detects un-dismissable
  modals — and reports runtime errors and interaction bugs.

All share one scan store, one `/api/scans` API, and one reporting pipeline.

## Architecture

```
EngineerOS
 ├─ Core Engine        (app lifecycle, config)
 ├─ Plugin Manager     (discovers + loads modules)
 ├─ Event Bus          (async pub/sub between modules)
 ├─ Scan Registry      (modules register a ScanRunner; core dispatches /api/scans)
 ├─ Scan Hub           (WebSocket progress fan-out)
 ├─ Shared AI Layer    (provider-agnostic: Anthropic / OpenAI / Ollama)
 ├─ Shared Memory      (SQLite now, Postgres-ready)
 ├─ Reporting Engine   (JSON / CSV / HTML / PDF, per-module branded)
 └─ Modules
     ├─ website_intelligence   (Module 1 — crawl + audit)
     ├─ repo_intelligence      (Module 2 — repository analysis)
     ├─ api_intelligence       (Module 3 — API discovery + OpenAPI/Postman)
     ├─ knowledge_graph        (Module 4 — component graph + AI summaries)
     ├─ ai_copilot             (Module 5 — grounded coding Q&A)
     └─ autonomous_qa          (Module 6 — autonomous interaction QA)
```

Every module implements a common `BaseModule` interface, registers a `ScanRunner` with the
core, and communicates through the `EventBus`. New scan modules drop in without touching the
core — `POST /api/scans` with `{"module": "...", "url": "..."}` dispatches to the right one.

## Tech stack

| Layer     | Choice                                                        |
|-----------|--------------------------------------------------------------|
| Backend   | Python 3.11+, FastAPI, async                                 |
| Crawling  | Playwright (Chromium)                                        |
| A11y      | Native in-page heuristics (alt/labels/ARIA/headings/…); axe-core pluggable |
| Perf      | Lighthouse CLI (optional — auto-skips if not installed)      |
| Storage   | SQLite (default) — Postgres-swappable via `DATABASE_URL`     |
| Jobs      | asyncio tasks + in-memory hub (Celery/Redis-swappable)       |
| Frontend  | Next.js 14 (App Router), TypeScript, Tailwind CSS            |
| Reports   | Jinja2 HTML → Playwright print-to-PDF                        |

> **Location note:** this project lives at `D:\EngineerOS` because the `C:` drive was
> full. Playwright's Chromium and the pip/temp caches are kept on `D:` too (see the run
> scripts). If you relocate the project, update `PLAYWRIGHT_BROWSERS_PATH` accordingly.

## Quick start — CLI (primary interface)

No servers needed. From PowerShell or cmd:

```powershell
cd D:\EngineerOS\backend

.\eos scan https://example.com                     # Module 1: crawl + audit a site
.\eos scan https://example.com -m qa               # Module 6: autonomous QA agent
.\eos scan D:\path\to\repo -m repo                 # Module 2: repository intelligence
.\eos scan https://github.com/user/repo -m repo    # ...or analyze a GitHub repo directly
.\eos scan https://example.com -m api              # Module 3: capture live APIs (XHR/fetch)
.\eos scan D:\path\to\repo -m api                  # Module 3: extract API routes from source
.\eos scan https://github.com/user/repo -m api     # ...OpenAPI + Postman written to artifacts/

.\eos scan D:\path\to\repo -m kg                   # Module 4: knowledge graph + AI summaries
.\eos scan D:\path\to\repo -m kg --max-nodes 20    # ...summarize more components (slower on local AI)

.\eos ask "how does auth work?" --repo D:\path\to\repo   # Module 5: grounded coding Q&A
.\eos chat --repo D:\path\to\repo                        # Module 5: interactive Copilot session
.\eos scan https://example.com -m qa -v            # verbose: recommendations + explored flows
.\eos scan https://example.com --lighthouse        # include Lighthouse on the entry page
.\eos scan https://example.com -f html,pdf,json,csv -o D:\EngineerOS\reports

# Scan the WHOLE site (within a limit) + Lighthouse, and write an HTML report:
.\eos scan https://example.com --max-pages 100 --max-depth 4 --lighthouse -f html

.\eos list                                          # scan history
.\eos report <id> -f pdf                            # re-export a past scan
.\eos modules                                       # what's available
```

The `eos` wrapper (`eos.cmd` / `eos.ps1`) sets `PLAYWRIGHT_BROWSERS_PATH` and the venv
python for you, and works from any directory. Reports default to `.\reports\` under the
current directory; scan history is stored centrally in `backend\engineeros.db` regardless
of where you run from.

### Scanning the whole site

| Flag | Meaning | Default |
|------|---------|---------|
| `--max-pages N` | Total page cap for the crawl | 15 |
| `--max-depth N` | How many link-hops deep from the entry page | 2 |
| `--lighthouse` | Also run Lighthouse **on the entry page** (~20–40s) | off |

The crawler breadth-first crawls same-origin pages, seeds from `sitemap.xml`
(recursing sitemap *indexes* to real page URLs), treats `www`↔apex as the same site,
and skips non-HTML files (`.xml`, `.pdf`, images…). Every crawled page is audited for
accessibility / SEO / responsiveness / console + network errors; Lighthouse runs once
on the entry page.

Reports are **specific, not vague** — each finding lists the exact offending items:
every broken link with its HTTP status **and the page(s) it was found on**, every failed
asset with its URL/status, and the actual console error texts.

### Browser & WAF-protected sites (Akamai / Cloudflare)

Headless browsers send a `HeadlessChrome` User-Agent that WAFs block with "Access
Denied". EngineerOS defaults (in `.env`) to a **headed, real Chrome** so live scans get
through — a visible Chrome window opens during the scan.

| Flag | Meaning |
|------|---------|
| `--headed` | Drive a visible browser (bypasses headless WAF blocks) |
| `--headless` | Force headless — faster, for sites that don't block |
| `--browser chrome\|msedge\|chromium` | Which browser to drive (installed vs bundled) |

```powershell
.\eos scan protected-site.com                      # uses headed Chrome default → gets past Akamai
.\eos scan internal-site.com --headless --browser chromium   # fast headless for unprotected sites
```

> Note: repeatedly scanning a WAF-protected production site from one IP can still trip
> rate/reputation blocking. For your own sites, allowlist the scanner in the WAF, or scan
> a staging environment. This is not a bot-evasion tool — it just uses a real browser.

### Local AI (Module 5 — Copilot)

`ask` / `chat` need a model server running. This machine uses a local `llama.cpp`
server (everything on `D:` — the `C:` drive is full):

```powershell
# 1. Start the model server (leave it running in its own window)
D:\EngineerOS\serve-ai.ps1        # serves Qwen2.5-Coder-3B on http://127.0.0.1:8080

# 2. In another terminal, ask away
cd D:\EngineerOS\backend
.\eos ask "what does the plugin manager do?" --repo D:\EngineerOS\backend
```

Config lives in `backend\.env` (`AI_PROVIDER=openai`, `OPENAI_BASE_URL=http://127.0.0.1:8080/v1`).
To use a cloud model instead, set `AI_PROVIDER=anthropic` (or `openai`) with the matching
API key and `AI_MODEL`. The 3B local model is fast enough for short questions but slow for
long answers on modest hardware — a cloud key is far quicker for heavy use.

### Setup (already done on this machine)

```powershell
cd D:\EngineerOS\backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
$env:PLAYWRIGHT_BROWSERS_PATH="D:\EngineerOS\.ms-playwright"
.\.venv\Scripts\python -m playwright install chromium
npm install -g lighthouse    # optional, for performance audits
```

## Optional — API server + web UI

The same engines are also exposed over HTTP for the web dashboard:

```powershell
cd D:\EngineerOS\backend ; .\run.ps1        # API on http://localhost:8000 (docs at /docs)
cd D:\EngineerOS\frontend ; npm run dev     # UI on http://localhost:3000
```

Configuration is optional — copy `.env.example` to `.env` to override defaults
(Postgres, Lighthouse, AI provider).

## API surface (Module 1)

| Method | Path                          | Purpose                          |
|--------|-------------------------------|----------------------------------|
| GET    | `/api/health`                 | Liveness + loaded modules        |
| GET    | `/api/modules`                | List loaded plugins              |
| POST   | `/api/scans`                  | Start a website scan             |
| GET    | `/api/scans`                  | List scans                       |
| GET    | `/api/scans/{id}`             | Scan status + summary            |
| GET    | `/api/scans/{id}/report.json` | Full JSON report                 |
| GET    | `/api/scans/{id}/report.csv`  | CSV export of findings           |
| GET    | `/api/scans/{id}/report.html` | Rendered HTML report             |
| GET    | `/api/scans/{id}/report.pdf`  | PDF report                       |
| WS     | `/api/scans/{id}/stream`      | Live progress events            |

See [`backend/README.md`](backend/README.md) for module-authoring details.
