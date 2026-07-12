"""Static API route extraction from repository source.

Recognizes the common route-declaration idioms across frameworks without executing
code: FastAPI/Flask/Django REST decorators, Express/Koa/Fastify method calls, NestJS
decorators, Spring mappings, Gin/Echo (Go), and Next.js file-based API routes. Heuristic
— it captures method + path + source location, which is enough to generate a spec and
flag undocumented/insecure endpoints.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".next", "dist", "build",
    "out", "target", "vendor", ".idea", ".vscode", "coverage", ".pytest_cache",
}
HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


@dataclass(slots=True)
class RepoRoute:
    method: str
    path: str
    framework: str
    source: str          # relpath:line
    handler: str = ""
    auth_hint: bool = False   # decorator/middleware suggesting auth on this route
    params: list[str] = field(default_factory=list)


# --- Python: FastAPI / Flask / Django REST -----------------------------------------
# @router.get("/x"), @app.post("/y"), @blueprint.route("/z", methods=["POST"])
PY_METHOD_DECOR = re.compile(
    r"""@(\w+)\.(get|post|put|patch|delete|head|options)\(\s*['"]([^'"]+)['"]""", re.I
)
PY_FLASK_ROUTE = re.compile(
    r"""@(\w+)\.route\(\s*['"]([^'"]+)['"]([^)]*)\)""", re.I
)
PY_METHODS_KW = re.compile(r"""methods\s*=\s*\[([^\]]*)\]""", re.I)
PY_DEF_AFTER = re.compile(r"\bdef\s+(\w+)")

# --- JS/TS: Express / Koa / Fastify / Router ----------------------------------------
JS_METHOD_CALL = re.compile(
    r"""\b(?:app|router|api|server|route|r|fastify)\.(get|post|put|patch|delete|head|options|all)\(\s*[`'"]([^`'"]+)[`'"]""",
    re.I,
)
# NestJS: @Get('x') @Post()
NEST_DECOR = re.compile(r"""@(Get|Post|Put|Patch|Delete|Head|Options)\(\s*[`'"]?([^`'")]*)[`'"]?\s*\)""")

# --- Java Spring --------------------------------------------------------------------
SPRING_MAPPING = re.compile(
    r"""@(Get|Post|Put|Patch|Delete|Request)Mapping\(\s*(?:value\s*=\s*)?[`'"]([^`'"]+)[`'"]""", re.I
)

# --- Go: Gin / Echo / Gorilla -------------------------------------------------------
GO_METHOD_CALL = re.compile(
    r"""\b\w+\.(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|Handle|HandleFunc)\(\s*(?:"([^"]*)"\s*,\s*)?["']?([^"',)]*)["']?""",
)

AUTH_HINT_RE = re.compile(
    r"(?i)\b(auth|authenticate|require_?auth|login_required|jwt|bearer|@?protected|"
    r"isAuthenticated|verifyToken|permission|IsAuthenticated|Secured|PreAuthorize|guard)\b"
)
PATH_PARAM_RE = re.compile(r"[:{<](\w+)[>}]?")


def _iter_source(root: Path, max_files: int) -> list[Path]:
    out: list[Path] = []
    stack = [root]
    exts = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go"}
    while stack and len(out) < max_files:
        cur = stack.pop()
        try:
            for entry in cur.iterdir():
                if entry.is_dir():
                    if entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                        stack.append(entry)
                elif entry.suffix.lower() in exts:
                    out.append(entry)
        except OSError:
            continue
    return out


def _params(path: str) -> list[str]:
    return PATH_PARAM_RE.findall(path)


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _in_comment(text: str, pos: int, markers: tuple[str, ...]) -> bool:
    """True if a line-comment marker (#, //) precedes `pos` on the same line.

    Prevents example decorators/route calls written inside comments from being
    reported as real endpoints.
    """
    line_start = text.rfind("\n", 0, pos) + 1
    prefix = text[line_start:pos]
    return any(marker in prefix for marker in markers)


def _next_file_route(root: Path, f: Path) -> RepoRoute | None:
    """Next.js file-based API route: pages/api/**/*.ts or app/api/**/route.ts."""
    rel = f.relative_to(root).as_posix()
    m = re.search(r"(?:^|/)(?:pages/api|app/api)/(.+?)(?:/route)?\.[jt]sx?$", rel)
    if not m:
        return None
    segment = m.group(1)
    segment = re.sub(r"\[\.\.\.(\w+)\]", r"{\1}", segment)  # catch-all
    segment = re.sub(r"\[(\w+)\]", r"{\1}", segment)        # dynamic
    segment = re.sub(r"/index$", "", segment)
    path = "/api/" + segment
    text = f.read_text(encoding="utf-8", errors="replace")
    # App Router exports named HTTP verbs; infer methods from exports if present.
    verbs = re.findall(r"export\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)", text)
    method = verbs[0] if verbs else "GET"
    return RepoRoute(
        method=method.upper(), path=path, framework="Next.js",
        source=f"{rel}:1", handler=segment,
        auth_hint=bool(AUTH_HINT_RE.search(text)), params=_params(path),
    )


def extract_repo_routes(root: Path, max_files: int = 6000) -> list[RepoRoute]:
    routes: list[RepoRoute] = []
    for f in _iter_source(root, max_files):
        rel = f.relative_to(root).as_posix()
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        suffix = f.suffix.lower()

        if suffix == ".py":
            for m in PY_METHOD_DECOR.finditer(text):
                if _in_comment(text, m.start(), ("#",)):
                    continue
                after = text[m.end(): m.end() + 200]
                handler = (PY_DEF_AFTER.search(after) or [None, ""])[1] if PY_DEF_AFTER.search(after) else ""
                fw = "FastAPI" if "fastapi" in text.lower() or "apirouter" in text.lower() else "Flask/Python"
                routes.append(RepoRoute(
                    method=m.group(2).upper(), path=m.group(3), framework=fw,
                    source=f"{rel}:{_line_of(text, m.start())}", handler=handler,
                    auth_hint=_has_auth_context(text, m.start()), params=_params(m.group(3)),
                ))
            for m in PY_FLASK_ROUTE.finditer(text):
                if _in_comment(text, m.start(), ("#",)):
                    continue
                methods_kw = PY_METHODS_KW.search(m.group(3))
                methods = (re.findall(r"['\"](\w+)['\"]", methods_kw.group(1)) if methods_kw else ["GET"])
                for method in methods:
                    routes.append(RepoRoute(
                        method=method.upper(), path=m.group(2), framework="Flask",
                        source=f"{rel}:{_line_of(text, m.start())}",
                        auth_hint=_has_auth_context(text, m.start()), params=_params(m.group(2)),
                    ))

        elif suffix in (".js", ".jsx", ".ts", ".tsx"):
            nx = _next_file_route(root, f)
            if nx:
                routes.append(nx)
            for m in JS_METHOD_CALL.finditer(text):
                if _in_comment(text, m.start(), ("//",)):
                    continue
                method = m.group(1).upper()
                methods = HTTP_METHODS if method == "ALL" else [method]
                for mth in methods:
                    routes.append(RepoRoute(
                        method=mth.upper(), path=m.group(2), framework="Express/Node",
                        source=f"{rel}:{_line_of(text, m.start())}",
                        auth_hint=_has_auth_context(text, m.start()), params=_params(m.group(2)),
                    ))
            if "@nestjs" in text or "@Controller" in text:
                prefix_m = re.search(r"""@Controller\(\s*[`'"]?([^`'")]*)""", text)
                prefix = ("/" + prefix_m.group(1).strip("/")) if prefix_m and prefix_m.group(1) else ""
                for m in NEST_DECOR.finditer(text):
                    if _in_comment(text, m.start(), ("//",)):
                        continue
                    sub = m.group(2).strip("/")
                    path = (prefix + ("/" + sub if sub else "")) or "/"
                    routes.append(RepoRoute(
                        method=m.group(1).upper(), path=path, framework="NestJS",
                        source=f"{rel}:{_line_of(text, m.start())}",
                        auth_hint=_has_auth_context(text, m.start()), params=_params(path),
                    ))

        elif suffix == ".java":
            base_m = re.search(r"""@RequestMapping\(\s*(?:value\s*=\s*)?[`'"]([^`'"]+)[`'"]""", text)
            base = base_m.group(1) if base_m else ""
            for m in SPRING_MAPPING.finditer(text):
                if _in_comment(text, m.start(), ("//",)):
                    continue
                verb = m.group(1)
                method = "GET" if verb == "Request" else verb.upper()
                path = (base.rstrip("/") + "/" + m.group(2).lstrip("/")) if base and m.group(2) != base else m.group(2)
                routes.append(RepoRoute(
                    method=method, path=path or "/", framework="Spring",
                    source=f"{rel}:{_line_of(text, m.start())}",
                    auth_hint=_has_auth_context(text, m.start()), params=_params(path or "/"),
                ))

        elif suffix == ".go":
            for m in GO_METHOD_CALL.finditer(text):
                if _in_comment(text, m.start(), ("//",)):
                    continue
                verb = m.group(1)
                if verb in ("Handle", "HandleFunc"):
                    continue
                path = m.group(2) or m.group(3) or ""
                if not path.startswith("/"):
                    continue
                routes.append(RepoRoute(
                    method=verb.upper(), path=path, framework="Go",
                    source=f"{rel}:{_line_of(text, m.start())}",
                    auth_hint=_has_auth_context(text, m.start()), params=_params(path),
                ))

    # Dedup by (method, path, source)
    seen: set[tuple[str, str, str]] = set()
    unique: list[RepoRoute] = []
    for r in routes:
        key = (r.method, r.path, r.source)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return sorted(unique, key=lambda r: (r.path, r.method))


def _has_auth_context(text: str, pos: int) -> bool:
    """Look for auth hints in the ~4 lines around a route declaration."""
    start = text.rfind("\n", 0, pos)
    for _ in range(4):
        start = text.rfind("\n", 0, start - 1) if start > 0 else 0
    window = text[max(0, start): pos + 160]
    return bool(AUTH_HINT_RE.search(window))
