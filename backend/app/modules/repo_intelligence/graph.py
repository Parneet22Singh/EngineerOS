"""Internal import graph for Python and JavaScript/TypeScript.

Builds a file-level dependency graph from import statements, then derives circular
dependencies and dead-code candidates (files nothing imports). Heuristic by design —
it resolves repo-internal imports only and ignores third-party packages.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

PY_FROM_IMPORT = re.compile(r"^\s*from\s+([\w.]+)\s+import\s+([^\n#(]+|\([^)]*\))", re.M)
PY_PLAIN_IMPORT = re.compile(r"^\s*import\s+([\w.]+)", re.M)
JS_IMPORT = re.compile(
    r"""(?:import\s+(?:[\w*{},\s]+\s+from\s+)?|require\(|import\()\s*['"]([^'"]+)['"]""", re.M
)
JS_EXTS = (".js", ".jsx", ".ts", ".tsx")

# Files that legitimately have no importers: entry points, tests, scripts, framework
# convention files (Next.js app/pages router), type declarations, shell launchers.
ENTRY_LIKE = re.compile(
    r"(^|/)(main|index|app|server|cli|manage|wsgi|asgi|setup|conftest|__main__|__init__)\.\w+$"
    r"|[._-](test|spec|config)\.\w+$|(^|/)test_|(^|/)(tests?|__tests__|scripts?|migrations)/"
    r"|(^|/)(app|pages)/.*(page|layout|route|template|loading|error|not-found|middleware)\.[jt]sx?$"
    r"|\.d\.ts$|\.(sh|ps1|cmd|bat)$",
    re.I,
)


def _resolve_py(module: str, importer: str, files: set[str]) -> str | None:
    """Resolve a dotted module path to a repo-relative file, if it's internal.

    Packages rarely live at the repo root (e.g. ``backend/app/...``), so every ancestor
    directory of the importer is tried as a potential source root. Relative imports
    (``from .foo import x``) resolve against the importer's own package.
    """
    importer_dir = importer.rsplit("/", 1)[0] if "/" in importer else ""

    if module.startswith("."):
        dots = len(module) - len(module.lstrip("."))
        rest = module.lstrip(".")
        base_parts = importer_dir.split("/") if importer_dir else []
        base_parts = base_parts[: len(base_parts) - (dots - 1)] if dots > 1 else base_parts
        parts = base_parts + (rest.split(".") if rest else [])
        for i in range(len(parts), max(len(base_parts) - 1, 0), -1):
            base = "/".join(parts[:i])
            for candidate in (f"{base}.py", f"{base}/__init__.py"):
                if candidate in files:
                    return candidate
        return None

    # Absolute import: try repo root plus every ancestor dir of the importer as source root.
    prefixes = [""]
    segs = importer_dir.split("/") if importer_dir else []
    for i in range(1, len(segs) + 1):
        prefixes.append("/".join(segs[:i]) + "/")
    parts = module.split(".")
    for i in range(len(parts), 0, -1):
        base = "/".join(parts[:i])
        for prefix in prefixes:
            for candidate in (f"{prefix}{base}.py", f"{prefix}{base}/__init__.py"):
                if candidate in files:
                    return candidate
    return None


def _resolve_js(spec: str, importer: str, files: set[str]) -> str | None:
    """Resolve a relative (or ``@/``-aliased) JS/TS import to a repo-relative file."""
    if spec.startswith("@/"):
        # tsconfig path alias — try the remainder against every ancestor dir of the importer.
        rest = spec[2:]
        segs = importer.split("/")[:-1]
        for i in range(len(segs), -1, -1):
            prefix = "/".join(segs[:i])
            base = f"{prefix}/{rest}" if prefix else rest
            hit = _try_js_candidates(base, files)
            if hit:
                return hit
        return None
    if not spec.startswith("."):
        return None  # bare specifier -> third-party
    base = (Path(importer).parent / spec).as_posix()
    # normalize ../ segments
    parts: list[str] = []
    for seg in base.split("/"):
        if seg == "..":
            if parts:
                parts.pop()
        elif seg not in (".", ""):
            parts.append(seg)
    base = "/".join(parts)
    return _try_js_candidates(base, files)


def _try_js_candidates(base: str, files: set[str]) -> str | None:
    candidates = [base] if Path(base).suffix else []
    candidates += [base + ext for ext in JS_EXTS] + [f"{base}/index{ext}" for ext in JS_EXTS]
    return next((c for c in candidates if c in files), None)


def build_import_graph(root: Path, source_files: list[Path]) -> dict[str, set[str]]:
    """relpath -> set of relpaths it imports."""
    rels = {f.relative_to(root).as_posix() for f in source_files}
    graph: dict[str, set[str]] = defaultdict(set)
    for f in source_files:
        rel = f.relative_to(root).as_posix()
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if f.suffix == ".py":
            for m in PY_PLAIN_IMPORT.finditer(text):
                target = _resolve_py(m.group(1), rel, rels)
                if target and target != rel:
                    graph[rel].add(target)
            for m in PY_FROM_IMPORT.finditer(text):
                module, names = m.group(1), m.group(2)
                # `from pkg import name` may bind a submodule: prefer pkg/name.py over
                # pkg/__init__.py so package re-exports don't read as false cycles.
                resolved_any = False
                for name in re.findall(r"[\w]+", names):
                    if name == "import":
                        continue
                    target = _resolve_py(f"{module}.{name}" if not module.endswith(".") else module + name,
                                         rel, rels)
                    if target and target != rel and not target.endswith("__init__.py"):
                        graph[rel].add(target)
                        resolved_any = True
                if not resolved_any:
                    target = _resolve_py(module, rel, rels)
                    if target and target != rel:
                        graph[rel].add(target)
        elif f.suffix in JS_EXTS:
            for m in JS_IMPORT.finditer(text):
                target = _resolve_js(m.group(1), rel, rels)
                if target and target != rel:
                    graph[rel].add(target)
    return dict(graph)


def find_cycles(graph: dict[str, set[str]], limit: int = 10) -> list[list[str]]:
    """Return up to `limit` distinct import cycles (as file paths)."""
    cycles: list[list[str]] = []
    seen_keys: set[frozenset[str]] = set()
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = defaultdict(int)
    stack: list[str] = []

    def dfs(node: str) -> None:
        if len(cycles) >= limit:
            return
        color[node] = GRAY
        stack.append(node)
        for dep in sorted(graph.get(node, ())):
            if color[dep] == GRAY:
                cycle = stack[stack.index(dep):] + [dep]
                key = frozenset(cycle)
                if key not in seen_keys:
                    seen_keys.add(key)
                    cycles.append(cycle)
            elif color[dep] == WHITE:
                dfs(dep)
        stack.pop()
        color[node] = BLACK

    for node in sorted(graph):
        if color[node] == WHITE:
            dfs(node)
    return cycles


def dead_file_candidates(graph: dict[str, set[str]], all_sources: list[str]) -> list[str]:
    """Source files that nothing in the repo imports (excluding entry-like files)."""
    imported: set[str] = set()
    for deps in graph.values():
        imported |= deps
    return sorted(
        f for f in all_sources
        if f not in imported and not ENTRY_LIKE.search(f)
    )
