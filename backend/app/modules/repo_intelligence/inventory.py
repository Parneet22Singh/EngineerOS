"""Repository inventory: files, languages, dependencies, frameworks, entry points,
and an architecture structure map — all derived statically, no AI required.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Directories that are never part of the project's own source.
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__",
    ".next", ".nuxt", "dist", "build", "out", "target", "vendor", ".idea", ".vscode",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "coverage", ".tox", "eggs",
    ".gradle", "bin", "obj", ".terraform", "bower_components", ".cache",
}

LANGUAGES: dict[str, str] = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript", ".java": "Java", ".kt": "Kotlin", ".go": "Go", ".rs": "Rust",
    ".cs": "C#", ".cpp": "C++", ".cc": "C++", ".c": "C", ".h": "C/C++ header",
    ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".scala": "Scala", ".sql": "SQL",
    ".sh": "Shell", ".ps1": "PowerShell", ".html": "HTML", ".css": "CSS",
    ".scss": "SCSS", ".vue": "Vue", ".svelte": "Svelte", ".dart": "Dart",
}
CODE_EXTS = {e for e, l in LANGUAGES.items() if l not in ("HTML", "CSS", "SCSS", "SQL")}

# dependency name (lowercased) -> framework label
FRAMEWORK_HINTS: dict[str, str] = {
    "react": "React", "next": "Next.js", "vue": "Vue", "@angular/core": "Angular",
    "svelte": "Svelte", "express": "Express", "koa": "Koa", "fastify": "Fastify",
    "nestjs": "NestJS", "@nestjs/core": "NestJS", "electron": "Electron",
    "fastapi": "FastAPI", "django": "Django", "flask": "Flask", "tornado": "Tornado",
    "celery": "Celery", "sqlalchemy": "SQLAlchemy", "pydantic": "Pydantic",
    "playwright": "Playwright", "pytest": "pytest", "rails": "Ruby on Rails",
    "spring-boot-starter": "Spring Boot", "gin-gonic/gin": "Gin", "gorilla/mux": "Gorilla",
    "laravel/framework": "Laravel", "actix-web": "Actix", "rocket": "Rocket",
    "tailwindcss": "Tailwind CSS", "typescript": "TypeScript",
}

# structure-map buckets: category -> path/name regexes (checked against posix relpath)
STRUCTURE_PATTERNS: dict[str, list[str]] = {
    "entry points": [r"(^|/)(main|index|app|server|cli|manage)\.(py|[jt]sx?)$", r"(^|/)cmd/"],
    "routing / api": [r"(^|/)(routes?|routers?|api|endpoints?|controllers?|views?|handlers?)(/|\.)"],
    "auth": [r"(^|/)(auth|login|oauth|jwt|session|permission)s?(/|\.|_)"],
    "database": [r"(^|/)(db|database|models?|schemas?|migrations?|repositor(y|ies)|entities|orm)(/|\.)"],
    "services / domain": [r"(^|/)(services?|domain|usecases?|core|engine)s?(/|\.)"],
    "queues / jobs": [r"(^|/)(queue|worker|job|task|celery|cron)s?(/|\.|_)"],
    "caching": [r"(^|/)(cache|redis)(/|\.|_)"],
    "config": [r"(^|/)(config|settings|env)s?(/|\.|_)"],
    "tests": [r"(^|/)(tests?|__tests__|spec)(/|\.|_)", r"[._](test|spec)\.[jt]sx?$", r"(^|/)test_.*\.py$"],
    "ci / deploy": [r"(^|/)(\.github|\.gitlab-ci|Jenkinsfile|Dockerfile|docker-compose|k8s|helm|\.circleci)"],
    "utilities": [r"(^|/)(utils?|helpers?|lib|common|shared)(/|\.)"],
}


@dataclass(slots=True)
class Inventory:
    files: list[Path] = field(default_factory=list)  # analyzable source files (absolute)
    total_files: int = 0
    total_lines: int = 0
    total_bytes: int = 0
    languages: list[dict] = field(default_factory=list)
    dependencies: dict[str, list[str]] = field(default_factory=dict)  # manifest -> deps
    frameworks: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    structure: dict[str, list[str]] = field(default_factory=dict)
    line_counts: dict[str, int] = field(default_factory=dict)  # relpath -> lines


def _iter_files(root: Path, max_files: int) -> list[Path]:
    found: list[Path] = []
    stack = [root]
    while stack and len(found) < max_files:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name))
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                    stack.append(entry)
                elif entry.name in (".github",):  # keep CI config visible
                    stack.append(entry)
            elif entry.is_file():
                found.append(entry)
                if len(found) >= max_files:
                    break
    return found


MANIFEST_NAMES = {
    "package.json", "requirements.txt", "requirements-dev.txt", "pyproject.toml",
    "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts",
    "composer.json", "Gemfile",
}
MANIFEST_MAX_DEPTH = 3  # monorepos keep manifests near the top


def _find_manifests(root: Path) -> list[Path]:
    found: list[Path] = []
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if depth < MANIFEST_MAX_DEPTH and entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                    stack.append((entry, depth + 1))
            elif entry.name in MANIFEST_NAMES:
                found.append(entry)
    return sorted(found, key=lambda p: len(p.parts))


def _parse_one_manifest(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    name = path.name
    if name in ("package.json", "composer.json"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        return (list(data.get("dependencies", {})) + list(data.get("devDependencies", {}))
                + list(data.get("require", {})))
    if name.startswith("requirements"):
        names = [
            re.split(r"[<>=!~\[;\s]", line.strip(), 1)[0]
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith(("#", "-"))
        ]
        return [n for n in names if n]
    if name == "pyproject.toml":
        names = re.findall(r'^\s*"([A-Za-z0-9_.\-]+)[<>=!~\[]', text, re.M)
        block = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.S)
        if block:
            names += re.findall(r'"([A-Za-z0-9_.\-]+)', block.group(1))
        return names
    if name == "go.mod":
        return re.findall(r"^\s*([\w./\-]+)\s+v[\d.]", text, re.M)
    if name == "Cargo.toml":
        block = re.search(r"\[dependencies\](.*?)(\n\[|\Z)", text, re.S)
        return re.findall(r"^([\w\-]+)\s*=", block.group(1), re.M) if block else []
    if name == "pom.xml":
        return re.findall(r"<artifactId>([\w.\-]+)</artifactId>", text)
    if name.startswith("build.gradle"):
        return re.findall(r"['\"]([\w.\-]+:[\w.\-]+):[\w.\-]+['\"]", text)
    if name == "Gemfile":
        return re.findall(r"^\s*gem\s+['\"]([\w\-]+)", text, re.M)
    return []


def _parse_manifests(root: Path) -> tuple[dict[str, list[str]], list[str]]:
    """Parse every dependency manifest in the tree (monorepo-aware, depth-limited)."""
    deps: dict[str, list[str]] = {}
    frameworks: set[str] = set()
    for path in _find_manifests(root):
        names = _parse_one_manifest(path)
        if not names:
            continue
        rel = path.relative_to(root).as_posix()
        deps[rel] = sorted(set(names))
        for n in names:
            for hint, label in FRAMEWORK_HINTS.items():
                if n.lower() == hint or n.lower().startswith(hint + "-") or hint in n.lower():
                    frameworks.add(label)
    return deps, sorted(frameworks)


def _detect_entry_points(root: Path, relfiles: list[str]) -> list[str]:
    entries: list[str] = []
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
            if data.get("main"):
                entries.append(f"{data['main']} (package.json main)")
            start = (data.get("scripts") or {}).get("start")
            if start:
                entries.append(f"npm start → {start}")
        except (json.JSONDecodeError, OSError):
            pass
    dockerfile = root / "Dockerfile"
    if dockerfile.is_file():
        for m in re.finditer(r"^(?:CMD|ENTRYPOINT)\s+(.+)$", dockerfile.read_text(encoding="utf-8", errors="replace"), re.M):
            entries.append(f"Dockerfile → {m.group(1).strip()[:80]}")
    common = re.compile(r"(^|/)(main|manage|cli|wsgi|asgi)\.py$|(^|/)(index|server|app|main)\.[jt]sx?$")
    entries.extend(rf for rf in relfiles if common.search(rf) and "test" not in rf)
    return entries[:12]


def build_inventory(root: Path, max_files: int = 5000) -> Inventory:
    inv = Inventory()
    all_files = _iter_files(root, max_files)
    inv.total_files = len(all_files)

    lang_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "lines": 0})
    relfiles: list[str] = []

    for f in all_files:
        rel = f.relative_to(root).as_posix()
        relfiles.append(rel)
        try:
            size = f.stat().st_size
        except OSError:
            continue
        inv.total_bytes += size
        lang = LANGUAGES.get(f.suffix.lower())
        if lang and size < 2_000_000:  # don't line-count generated monsters
            try:
                lines = f.read_text(encoding="utf-8", errors="replace").count("\n") + 1
            except OSError:
                continue
            lang_stats[lang]["files"] += 1
            lang_stats[lang]["lines"] += lines
            inv.total_lines += lines
            inv.line_counts[rel] = lines
            if f.suffix.lower() in CODE_EXTS:
                inv.files.append(f)

    total = max(inv.total_lines, 1)
    inv.languages = sorted(
        (
            {"name": lang, "files": s["files"], "lines": s["lines"], "pct": round(100 * s["lines"] / total, 1)}
            for lang, s in lang_stats.items()
        ),
        key=lambda x: -x["lines"],
    )
    inv.dependencies, inv.frameworks = _parse_manifests(root)
    inv.entry_points = _detect_entry_points(root, relfiles)

    structure: dict[str, list[str]] = {}
    for category, patterns in STRUCTURE_PATTERNS.items():
        rx = [re.compile(p, re.I) for p in patterns]
        hits = [rf for rf in relfiles if any(r.search(rf) for r in rx)]
        if hits:
            structure[category] = hits[:10]
    inv.structure = structure
    return inv
