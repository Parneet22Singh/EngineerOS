"""EngineerOS command-line interface.

Runs scans directly in-process — no API server or frontend required.

Usage examples::

    eos scan https://example.com                     # website intelligence crawl
    eos scan https://example.com -m qa               # autonomous QA agent
    eos scan https://example.com -m qa --lighthouse --out reports
    eos list                                          # scan history
    eos report <scan-id> --format html,pdf            # re-export reports
    eos modules                                       # list available modules

Invoke as ``python -m app.cli ...`` or via the ``eos`` wrapper scripts.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

from colorama import Fore, Style, just_fix_windows_console

# --- terminal setup -------------------------------------------------------------
just_fix_windows_console()
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SEV_COLOR = {
    "critical": Fore.RED + Style.BRIGHT,
    "high": Fore.RED,
    "medium": Fore.YELLOW,
    "low": Fore.CYAN,
    "info": Fore.WHITE + Style.DIM,
}
MODULE_ALIASES = {
    "web": "website_intelligence",
    "website": "website_intelligence",
    "website_intelligence": "website_intelligence",
    "qa": "autonomous_qa",
    "agent": "autonomous_qa",
    "autonomous_qa": "autonomous_qa",
    "repo": "repo_intelligence",
    "repository": "repo_intelligence",
    "repo_intelligence": "repo_intelligence",
    "api": "api_intelligence",
    "api_intelligence": "api_intelligence",
    "kg": "knowledge_graph",
    "graph": "knowledge_graph",
    "knowledge": "knowledge_graph",
    "knowledge_graph": "knowledge_graph",
}
ALL_FORMATS = ("json", "csv", "html", "pdf")


def _c(text: str, color: str) -> str:
    return f"{color}{text}{Style.RESET_ALL}"


MODULE_TITLES = {
    "website_intelligence": "Website Intelligence",
    "autonomous_qa": "Autonomous QA Agent",
    "repo_intelligence": "Repository Intelligence",
    "api_intelligence": "API Intelligence",
    "knowledge_graph": "Knowledge Graph",
}


def _banner(module: str, target: str) -> None:
    title = MODULE_TITLES.get(module, module)
    print()
    print(_c("  EngineerOS", Fore.MAGENTA + Style.BRIGHT) + _c(f" · {title}", Style.DIM))
    print(_c(f"  ▶ {target}", Fore.WHITE + Style.BRIGHT))
    print()


def _progress_line(stage: str, progress: float, detail: str) -> None:
    width = 28
    filled = int(width * max(0.0, min(1.0, progress)))
    bar = "█" * filled + "░" * (width - filled)
    detail = (detail or "")[:60]
    line = f"  {Fore.MAGENTA}{bar}{Style.RESET_ALL} {progress:>4.0%}  {stage:<14} {Style.DIM}{detail}{Style.RESET_ALL}"
    print(f"\r{line:<120}", end="", flush=True)


# --- commands -------------------------------------------------------------------

async def cmd_scan(args: argparse.Namespace) -> int:
    # Imports deferred so `eos --help` stays instant.
    from app.config import get_settings
    from app.core.event_bus import EventBus
    from app.db.database import SessionLocal, init_db
    from app.db.models import Scan, ScanStatus

    module = MODULE_ALIASES.get(args.module.lower())
    if module is None:
        print(_c(f"error: unknown module '{args.module}' (use: web, qa)", Fore.RED))
        return 2

    if module in ("repo_intelligence", "knowledge_graph"):
        # Target is a local directory or a git URL — no https:// prefixing of paths.
        p = Path(args.url)
        if p.exists() and p.is_dir():
            target = str(p.resolve())
        elif args.url.startswith(("http://", "https://", "git@", "ssh://")):
            target = args.url
        else:
            print(_c(f"error: '{args.url}' is neither an existing directory nor a git URL", Fore.RED))
            return 2
    elif module == "api_intelligence":
        # Accepts a live URL (web capture) or a local dir / git URL (route extraction).
        p = Path(args.url)
        if p.exists() and p.is_dir():
            target = str(p.resolve())
        elif args.url.startswith(("http://", "https://", "git@", "ssh://")):
            target = args.url
        else:
            target = f"https://{args.url}"
    else:
        target = args.url if args.url.startswith(("http://", "https://")) else f"https://{args.url}"

    settings = get_settings()
    await init_db()
    bus = EventBus()

    options: dict = {"run_lighthouse": args.lighthouse}
    # Browser overrides (apply to any module that drives a browser). Left as None
    # unless the user passes a flag, so the configured .env defaults win otherwise.
    if args.headed:
        options["browser_headed"] = True
    elif args.headless:
        options["browser_headed"] = False
    if getattr(args, "browser", None):
        options["browser_channel"] = "" if args.browser == "chromium" else args.browser
    if module == "website_intelligence":
        options.update(
            max_pages=args.max_pages,
            max_depth=args.max_depth,
            respect_robots=not args.ignore_robots,
        )
    elif module == "autonomous_qa":
        options.update(max_actions=args.max_actions)
    elif module == "repo_intelligence":
        options.update(max_files=args.max_files)
    elif module == "knowledge_graph":
        options.update(max_files=args.max_files, max_nodes=getattr(args, "max_nodes", 12))
    else:  # api_intelligence
        options.update(max_files=args.max_files, max_pages=args.max_pages,
                       mode=getattr(args, "api_mode", None))

    scan_id = uuid.uuid4().hex
    async with SessionLocal() as session:
        session.add(Scan(id=scan_id, module=module, target=target,
                         status=ScanStatus.queued, options=options))
        await session.commit()

    _banner(module, target)

    async def progress(stage: str, prog: float, detail: str = "") -> None:
        _progress_line(stage, prog, detail)

    if module == "website_intelligence":
        from app.modules.website_intelligence.engine import ScanEngine
        engine = ScanEngine(settings, bus, progress_cb=progress)
    elif module == "autonomous_qa":
        from app.modules.autonomous_qa.engine import QAEngine
        engine = QAEngine(settings, bus, progress)
    elif module == "repo_intelligence":
        from app.modules.repo_intelligence.engine import RepoEngine
        engine = RepoEngine(settings, bus, progress)
    elif module == "knowledge_graph":
        from app.modules.knowledge_graph.engine import KnowledgeGraphEngine
        engine = KnowledgeGraphEngine(settings, bus, progress)
    else:
        from app.modules.api_intelligence.engine import APIEngine
        engine = APIEngine(settings, bus, progress)

    await engine.run(scan_id, target, options)
    print()  # end the progress line

    code = await _print_result(scan_id, settings, args)
    return code


async def _print_result(scan_id: str, settings, args: argparse.Namespace) -> int:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.db.database import SessionLocal
    from app.db.models import Scan

    async with SessionLocal() as session:
        result = await session.execute(
            select(Scan).options(selectinload(Scan.findings)).where(Scan.id == scan_id)
        )
        scan = result.scalar_one()

        if scan.status.value == "failed":
            print(_c(f"\n  ✗ scan failed: {scan.error}", Fore.RED))
            return 1

        summary = scan.summary or {}
        by_sev = summary.get("by_severity", {})
        health = summary.get("health_score", "—")

        print()
        # Health is not meaningful for graph/mapping scans (no pass/fail findings).
        health_bit = ""
        if health is not None:
            health_bit = f"  {_c('Health', Style.DIM)}  {_c(str(health) + '/100', Fore.GREEN + Style.BRIGHT if isinstance(health, int) and health >= 90 else Fore.YELLOW + Style.BRIGHT)}    "
        print(f"{health_bit}{_c('Findings', Style.DIM)}  {summary.get('total_findings', len(scan.findings))}"
              + (f"    {_c('Pages', Style.DIM)}  {summary.get('pages_scanned')}" if summary.get("pages_scanned") else "")
              + (f"    {_c('Interactions', Style.DIM)}  {summary.get('actions_performed')}" if summary.get("actions_performed") is not None else ""))
        sev_bits = [
            _c(f"{name} {by_sev.get(name, 0)}", SEV_COLOR[name])
            for name in ("critical", "high", "medium", "low", "info")
        ]
        print("  " + "  ".join(sev_bits))
        print()

        # Repository overview (repo module)
        repo = summary.get("repo")
        if repo:
            langs = ", ".join(f"{l['name']} {l['pct']}%" for l in repo.get("languages", [])[:5])
            print(f"  {_c('Stack', Style.DIM)}      {', '.join(repo.get('frameworks') or ['—'])}")
            print(f"  {_c('Languages', Style.DIM)}  {langs or '—'}")
            print(f"  {_c('Size', Style.DIM)}       {repo.get('total_files')} files · "
                  f"{repo.get('total_lines'):,} lines · {repo.get('size_mb')} MB")
            g = repo.get("graph", {})
            print(f"  {_c('Graph', Style.DIM)}      {g.get('nodes')} modules · {g.get('edges')} imports · "
                  f"{g.get('cycles')} cycle(s) · {g.get('dead_candidates')} dead-code candidate(s)")
            if repo.get("entry_points"):
                print(f"  {_c('Entry', Style.DIM)}      {repo['entry_points'][0]}")
            if args.verbose and repo.get("structure"):
                print()
                print(_c("  Structure map:", Style.BRIGHT))
                for cat, paths in repo["structure"].items():
                    print(f"   {_c(cat + ':', Fore.MAGENTA)} {', '.join(paths[:4])}"
                          + (_c(f"  (+{len(paths) - 4} more)", Style.DIM) if len(paths) > 4 else ""))
            print()

        # Findings table
        for f in scan.findings:
            sev = f.severity.value
            print(f"  {_c(f'[{sev:<8}]', SEV_COLOR[sev])} {_c(f.category, Style.DIM):<22} {f.title}")
            if args.verbose:
                if f.recommendation:
                    print(f"             {_c('↳ ' + f.recommendation, Fore.GREEN)}")
                if f.element:
                    print(f"             {_c('element: ' + f.element[:100], Style.DIM)}")
        if not scan.findings:
            print(_c("  ✓ no issues found", Fore.GREEN))

        # API overview (api module)
        api = summary.get("api")
        if api:
            methods = "  ".join(f"{m} {n}" for m, n in api.get("by_method", {}).items())
            print(f"  {_c('Source', Style.DIM)}     {api.get('source')}"
                  + (f"    {_c('Base', Style.DIM)}  {api.get('base_url')}" if api.get("base_url") else ""))
            print(f"  {_c('Endpoints', Style.DIM)}  {api.get('endpoint_count')}    {methods}")
            if api.get("frameworks"):
                print(f"  {_c('Frameworks', Style.DIM)} {', '.join(api['frameworks'])}")
            if api.get("graphql_operations"):
                print(f"  {_c('GraphQL', Style.DIM)}    {', '.join(api['graphql_operations'][:8])}")
            if api.get("third_party"):
                print(f"  {_c('3rd-party', Style.DIM)}  {', '.join(api['third_party'][:6])}")
            print()
            print(_c("  Endpoints:", Style.BRIGHT))
            for e in api.get("endpoints", [])[: (60 if args.verbose else 20)]:
                status = f" {_c(str(e['status']), Fore.GREEN if (e.get('status') or 0) < 400 else Fore.RED)}" if e.get("status") else ""
                auth = _c(f" [{e['auth']}]", Fore.YELLOW) if e.get("auth") not in ("none", "", None) else ""
                loc = _c(f"  {e['source']}", Style.DIM) if args.verbose and e.get("source") else ""
                kind = _c(f" {e['kind']}", Fore.MAGENTA) if e.get("kind") == "GraphQL" else ""
                method_cell = _c(e["method"].ljust(6), Fore.CYAN)
                print(f"   {method_cell} {e['path']}{kind}{status}{auth}{loc}")
            extra = api.get("endpoint_count", 0) - len(api.get("endpoints", [])[: (60 if args.verbose else 20)])
            if extra > 0:
                print(_c(f"   … +{extra} more (see JSON report)", Style.DIM))
            arts = api.get("artifacts", {})
            if arts:
                adir = (settings.artifacts_dir).as_posix()
                print()
                print(f"  {_c('OpenAPI', Fore.GREEN)}  {adir}/{arts.get('openapi')}")
                print(f"  {_c('Postman', Fore.GREEN)}  {adir}/{arts.get('postman')}")
            print()

        # Knowledge graph overview (kg module)
        kg = summary.get("knowledge_graph")
        if kg:
            print(f"  {_c('Components', Style.DIM)} {kg.get('node_count')}    "
                  f"{_c('Relationships', Style.DIM)} {kg.get('edge_count')}    "
                  f"{_c('Cycles', Style.DIM)} {kg.get('cycle_count')}")
            if kg.get("frameworks"):
                print(f"  {_c('Stack', Style.DIM)}      {', '.join(kg['frameworks'])}")
            prov = kg.get("ai_provider")
            print(f"  {_c('Summaries', Style.DIM)}  {kg.get('summarized')} component(s) "
                  + (_c(f"via {prov}", Style.DIM) if prov and prov != "none"
                     else _c("(AI off — structure only)", Fore.YELLOW)))
            print()
            print(_c("  Key components (by connectivity):", Style.BRIGHT))
            for c in kg.get("components", [])[: (25 if args.verbose else 12)]:
                deg = _c(f"in {c['in_degree']}/out {c['out_degree']}", Style.DIM)
                print(f"   {_c(c['id'], Fore.CYAN)}  {deg}")
                if c.get("summary"):
                    print(f"      {_c(c['summary'], Style.DIM)}")
            adir = settings.artifacts_dir.as_posix()
            if kg.get("artifact"):
                print()
                print(f"  {_c('Graph', Fore.GREEN)}    {adir}/{kg.get('artifact')}")
            print()

        # Explored flows (QA module)
        flows = summary.get("flows") or []
        if flows and args.verbose:
            print()
            print(_c("  Explored flows:", Style.BRIGHT))
            for fl in flows:
                mark = _c("⚠", Fore.YELLOW) if fl["issue"] else _c("✓", Fore.GREEN)
                print(f"   {mark} {fl['action']:<14} {fl['label'][:40]:<42} {_c(fl['result'], Style.DIM)}")

        # Reports
        formats = [f.strip().strip('"\'') for f in args.format.split(",") if f.strip().strip('"\'')] if args.format else []
        bad = [f for f in formats if f not in ALL_FORMATS]
        if bad:
            print(_c(f"\n  unknown format(s): {', '.join(bad)} (choose from {', '.join(ALL_FORMATS)})", Fore.RED))
            return 2
        if formats:
            from app import reporting
            out_dir = Path(args.out).resolve()
            out_dir.mkdir(parents=True, exist_ok=True)
            print()
            for fmt in formats:
                dest = out_dir / f"engineeros-{scan_id[:8]}.{fmt}"
                if fmt == "json":
                    import json
                    dest.write_text(json.dumps(reporting.report_payload(scan, scan.findings), indent=2),
                                    encoding="utf-8")
                elif fmt == "csv":
                    dest.write_text(reporting.findings_to_csv(scan.findings), encoding="utf-8")
                elif fmt == "html":
                    dest.write_text(reporting.render_html(scan, scan.findings, settings), encoding="utf-8")
                elif fmt == "pdf":
                    dest.write_bytes(await reporting.render_pdf(scan, scan.findings, settings))
                print(f"  {_c('saved', Fore.GREEN)} {dest}")

        print()
        print(_c(f"  scan id: {scan_id}   (re-export any time: eos report {scan_id[:8]})", Style.DIM))
        return 0


async def cmd_list(args: argparse.Namespace) -> int:
    from sqlalchemy import select
    from app.db.database import SessionLocal, init_db
    from app.db.models import Scan

    await init_db()
    async with SessionLocal() as session:
        result = await session.execute(select(Scan).order_by(Scan.created_at.desc()).limit(args.limit))
        scans = list(result.scalars())
    if not scans:
        print("no scans yet — run: eos scan https://example.com")
        return 0
    print()
    print(_c(f"  {'ID':<10}{'MODULE':<12}{'STATUS':<12}{'FINDINGS':<10}{'HEALTH':<8}TARGET", Style.DIM))
    for s in scans:
        summary = s.summary or {}
        mod = {"autonomous_qa": "qa", "website_intelligence": "web", "repo_intelligence": "repo"}.get(s.module, s.module)
        status_color = {"completed": Fore.GREEN, "failed": Fore.RED, "running": Fore.YELLOW}.get(
            s.status.value, Style.DIM)
        print(f"  {s.id[:8]:<10}{mod:<12}{_c(f'{s.status.value:<12}', status_color)}"
              f"{str(summary.get('total_findings', '—')):<10}{str(summary.get('health_score', '—')):<8}{s.target}")
    print()
    return 0


async def cmd_report(args: argparse.Namespace) -> int:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.config import get_settings
    from app.db.database import SessionLocal, init_db
    from app.db.models import Scan

    await init_db()
    settings = get_settings()
    async with SessionLocal() as session:
        result = await session.execute(
            select(Scan).options(selectinload(Scan.findings)).where(Scan.id.like(f"{args.scan_id}%"))
        )
        scans = list(result.scalars())
    if not scans:
        print(_c(f"no scan matching '{args.scan_id}' — see: eos list", Fore.RED))
        return 1
    if len(scans) > 1:
        print(_c(f"'{args.scan_id}' is ambiguous ({len(scans)} matches) — use more characters", Fore.RED))
        return 1
    args.verbose = True
    return await _print_result(scans[0].id, settings, args)


async def cmd_modules(_: argparse.Namespace) -> int:
    print()
    print(f"  {_c('web', Fore.MAGENTA + Style.BRIGHT)}  Universal Website Intelligence")
    print(_c("       crawl · screenshots · a11y · seo · responsive · broken links · lighthouse", Style.DIM))
    print(f"  {_c('qa', Fore.MAGENTA + Style.BRIGHT)}   Autonomous QA Agent")
    print(_c("       explores a page: clicks buttons, opens menus, fills & submits forms,", Style.DIM))
    print(_c("       handles modals/dialogs, catches runtime errors", Style.DIM))
    print(f"  {_c('repo', Fore.MAGENTA + Style.BRIGHT)} Universal Repository Intelligence")
    print(_c("       local path or git URL: stack + language inventory, entry points,", Style.DIM))
    print(_c("       structure map, import graph, circular deps, dead code, secrets, smells", Style.DIM))
    print(f"  {_c('api', Fore.MAGENTA + Style.BRIGHT)}  Universal API Intelligence")
    print(_c("       live URL (network capture) or repo/git URL (route extraction):", Style.DIM))
    print(_c("       REST + GraphQL discovery, auth detection, broken/insecure/duplicate", Style.DIM))
    print(_c("       endpoints, OpenAPI 3.0 + Postman collection generation", Style.DIM))
    print(f"  {_c('kg', Fore.MAGENTA + Style.BRIGHT)}   Knowledge Graph")
    print(_c("       local path or git URL: component graph (imports/relationships),", Style.DIM))
    print(_c("       connectivity metrics, cycles, + AI one-line summaries of key files.", Style.DIM))
    print(f"  {_c('ask/chat', Fore.MAGENTA + Style.BRIGHT)} AI Copilot")
    print(_c("       grounded coding Q&A over a local (or cloud) model; cites repo files.", Style.DIM))
    print(_c("       e.g.  eos ask \"how does auth work?\" --repo D:\\path\\to\\repo", Style.DIM))
    print()
    return 0


async def _run_copilot(args, question: str, copilot, repo) -> None:
    import time
    where = f" · grounded in {repo.name}" if repo else ""
    print(f"\n  {_c('you', Fore.CYAN + Style.BRIGHT)}  {question}")
    print(_c(f"  thinking{where}… (local model — this can take a bit)", Style.DIM))
    t0 = time.time()
    ans = await copilot.ask(question, repo=repo, max_files=args.files, max_tokens=args.max_tokens)
    dt = time.time() - t0
    if ans.error:
        print(_c(f"\n  ✗ {ans.error}", Fore.RED))
        return
    tag = _c("copilot", Fore.MAGENTA + Style.BRIGHT)
    print(f"\n  {tag}")
    for line in ans.text.splitlines():
        print(f"  {line}")
    if ans.sources:
        print(_c(f"\n  sources: {', '.join(ans.sources)}", Style.DIM))
    print(_c(f"  ({dt:.0f}s{'· grounded' if ans.grounded else ''})", Style.DIM))


def _build_copilot():
    from app.config import get_settings
    from app.ai.provider import build_provider
    from app.modules.ai_copilot.copilot import Copilot
    settings = get_settings()
    provider = build_provider(settings)
    return Copilot(provider), provider, settings


def _resolve_repo(raw: str | None):
    if not raw:
        return None
    p = Path(raw)
    if not p.is_dir():
        print(_c(f"error: --repo '{raw}' is not a directory", Fore.RED))
        return False
    return p.resolve()


async def cmd_ask(args: argparse.Namespace) -> int:
    copilot, provider, _ = _build_copilot()
    if not provider.available:
        print(_c("  ✗ No AI provider configured. Set AI_PROVIDER in backend/.env and start the "
                 "model server (D:\\EngineerOS\\serve-ai.ps1).", Fore.RED))
        return 2
    repo = _resolve_repo(args.repo)
    if repo is False:
        return 2
    print(f"\n{_c('  EngineerOS', Fore.MAGENTA + Style.BRIGHT)}{_c(' · AI Copilot', Style.DIM)}")
    await _run_copilot(args, args.question, copilot, repo)
    print()
    return 0


async def cmd_chat(args: argparse.Namespace) -> int:
    copilot, provider, _ = _build_copilot()
    if not provider.available:
        print(_c("  ✗ No AI provider configured. Set AI_PROVIDER in backend/.env and start the "
                 "model server (D:\\EngineerOS\\serve-ai.ps1).", Fore.RED))
        return 2
    repo = _resolve_repo(args.repo)
    if repo is False:
        return 2
    print(f"\n{_c('  EngineerOS', Fore.MAGENTA + Style.BRIGHT)}{_c(' · AI Copilot (chat)', Style.DIM)}")
    scope = f"grounded in {repo}" if repo else "general coding (no repo)"
    print(_c(f"  {scope}. Type your question, or 'exit' to quit.", Style.DIM))
    while True:
        try:
            q = input(f"\n  {_c('you', Fore.CYAN + Style.BRIGHT)}  ").strip()
        except (EOFError, KeyboardInterrupt):
            print(_c("\n  bye", Fore.YELLOW))
            return 0
        if q.lower() in ("exit", "quit", ":q"):
            print(_c("  bye", Fore.YELLOW))
            return 0
        if not q:
            continue
        await _run_copilot(args, q, copilot, repo)


# --- entrypoint -------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="eos", description="EngineerOS — scan websites from your terminal.")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="run a scan against a URL, repo path, or git URL")
    s.add_argument("url", help="target: URL (web/qa) or local path / git URL (repo)")
    s.add_argument("-m", "--module", default="web",
                   help="web (crawl+audit, default), qa (autonomous agent), repo (repository analysis), "
                        "or api (API discovery + OpenAPI/Postman)")
    s.add_argument("--max-pages", type=int, default=15, help="crawl page budget (web module)")
    s.add_argument("--max-depth", type=int, default=2, help="crawl depth (web module)")
    s.add_argument("--max-actions", type=int, default=18, help="interaction budget (qa module)")
    s.add_argument("--max-files", type=int, default=5000, help="file budget (repo/api module)")
    s.add_argument("--api-mode", choices=["web", "repo"], default=None,
                   help="force API discovery mode (default: auto from target)")
    s.add_argument("--max-nodes", type=int, default=12,
                   help="components to AI-summarize in the knowledge graph (kg module)")
    s.add_argument("--lighthouse", action="store_true", help="also run Lighthouse (needs lighthouse CLI)")
    s.add_argument("--headed", action="store_true",
                   help="drive a visible browser (bypasses Akamai/Cloudflare headless blocks)")
    s.add_argument("--headless", action="store_true",
                   help="force headless (faster; overrides the configured default)")
    s.add_argument("--browser", choices=["chromium", "chrome", "msedge"], default=None,
                   help="browser to drive: chromium (bundled) | chrome | msedge (your installed browser)")
    s.add_argument("--ignore-robots", action="store_true", help="do not respect robots.txt (web module)")
    s.add_argument("-f", "--format", default="html", help="report formats: html,pdf,json,csv (comma-separated; empty for none)")
    s.add_argument("-o", "--out", default="reports", help="output directory for reports (default: ./reports)")
    s.add_argument("-v", "--verbose", action="store_true", help="show recommendations, elements, and QA flows")
    s.set_defaults(func=cmd_scan)

    ls = sub.add_parser("list", help="show scan history")
    ls.add_argument("-n", "--limit", type=int, default=20)
    ls.set_defaults(func=cmd_list)

    r = sub.add_parser("report", help="re-print + re-export a past scan")
    r.add_argument("scan_id", help="scan id (or unique prefix) from 'eos list'")
    r.add_argument("-f", "--format", default="", help="report formats to export: html,pdf,json,csv")
    r.add_argument("-o", "--out", default="reports", help="output directory for reports")
    r.set_defaults(func=cmd_report)

    m = sub.add_parser("modules", help="list available scan modules")
    m.set_defaults(func=cmd_modules)

    ask = sub.add_parser("ask", help="ask the AI Copilot a coding question (optionally about a repo)")
    ask.add_argument("question", help="your question (quote it)")
    ask.add_argument("--repo", default=None, help="repo path to ground the answer in (optional)")
    ask.add_argument("--files", type=int, default=6, help="max source files to pull as context")
    ask.add_argument("--max-tokens", type=int, default=512, help="response length cap")
    ask.set_defaults(func=cmd_ask)

    chat = sub.add_parser("chat", help="interactive AI Copilot session")
    chat.add_argument("--repo", default=None, help="repo path to ground answers in (optional)")
    chat.add_argument("--files", type=int, default=6, help="max source files to pull as context")
    chat.add_argument("--max-tokens", type=int, default=512, help="response length cap")
    chat.set_defaults(func=cmd_chat)
    return p


def _silence_proactor_shutdown_noise() -> None:
    """Suppress Windows asyncio ProactorEventLoop 'unclosed transport' spew.

    On Windows, transports created for subprocesses/sockets can be garbage-collected
    after the event loop closes, and their __del__ raises 'I/O operation on closed
    pipe' which the interpreter prints as an 'Exception ignored' traceback. It is
    entirely cosmetic and happens at shutdown; hide only this specific case.
    """
    prev = sys.unraisablehook

    def hook(unraisable):  # pragma: no cover - shutdown-only path
        exc = unraisable.exc_value
        text = f"{unraisable.object!r}{exc!r}"
        if isinstance(exc, ValueError) and "closed pipe" in str(exc):
            return
        if "_ProactorBasePipeTransport" in text or "BaseSubprocessTransport" in text:
            return
        prev(unraisable)

    sys.unraisablehook = hook


def main() -> None:
    _silence_proactor_shutdown_noise()
    args = build_parser().parse_args()
    try:
        code = asyncio.run(args.func(args))
    except KeyboardInterrupt:
        print(_c("\n  interrupted", Fore.YELLOW))
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()
