# EngineerOS Backend

FastAPI core + pluggable modules. See the [top-level README](../README.md) for setup.

## Layout

```
app/
├─ main.py                 # app factory + lifespan; boots core, mounts modules
├─ config.py               # Settings (env-driven)
├─ core/
│  ├─ event_bus.py         # async pub/sub
│  ├─ base_module.py       # BaseModule interface + ModuleContext
│  └─ plugin_manager.py    # discovers app.modules.*, mounts routers
├─ ai/provider.py          # provider-agnostic AI layer (anthropic/openai/ollama/none)
├─ db/                     # SQLAlchemy async engine, models, schemas
└─ modules/
   └─ website_intelligence/   # Module 1 (+ Module 6 QA core)
      ├─ module.py             # BaseModule impl + API router
      ├─ engine.py            # scan orchestrator
      ├─ crawler.py           # Playwright BFS crawler
      ├─ page_audit.py        # in-page a11y/SEO/DOM checks
      ├─ linkcheck.py         # broken links / redirect loops
      ├─ lighthouse.py        # Lighthouse CLI runner (optional)
      ├─ reporting.py         # JSON/CSV/HTML/PDF
      └─ hub.py               # WebSocket progress fan-out
```

## Writing a new module

1. Create `app/modules/<your_module>/` with an `__init__.py` that exports
   `MODULE = YourModule`.
2. Subclass `BaseModule`; implement `info` (metadata) and `router()` (FastAPI routes).
3. Optionally implement `startup()` / `shutdown()` and subscribe to the event bus.

The plugin manager auto-discovers it on boot — no core changes needed. Persist analysis
results into the shared `Scan` / `Finding` tables so the future intelligence layer can
reason across modules.

## Events

Module 1 emits: `scan.started`, `scan.progress`, `scan.completed`, `scan.failed`.
Subscribe via `context.event_bus.subscribe(topic, handler)`.

## Tests

```bash
pip install pytest pytest-asyncio
pytest
```
