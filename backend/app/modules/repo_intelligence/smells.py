"""Code smell and risk detectors: oversized files/functions, hardcoded secrets,
committed env files, and TODO/FIXME debt. All regex-heuristic, language-aware where
cheap (Python function lengths via indentation).
"""
from __future__ import annotations

import re
from pathlib import Path

from app.modules.website_intelligence.results import RawFinding

# (name, regex, is_high_confidence) — value is never included in evidence.
SECRET_PATTERNS: list[tuple[str, re.Pattern, bool]] = [
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), True),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), True),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), True),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"), True),
    ("Generic API key/secret assignment",
     re.compile(r"""(?i)\b(api[_-]?key|secret[_-]?key|auth[_-]?token|password)\s*[:=]\s*['"][^'"\s]{12,}['"]"""),
     False),
]
PLACEHOLDER = re.compile(r"(?i)example|sample|placeholder|your[_-]|xxx|changeme|<[^>]+>|\$\{|%\(|\{\{")

TODO_RX = re.compile(r"(?i)\b(TODO|FIXME|HACK|XXX)\b")
PY_DEF = re.compile(r"^([ \t]*)(?:async\s+)?def\s+(\w+)", re.M)

FILE_LINES_WARN = 600
FILE_LINES_BAD = 1200
FUNC_LINES_WARN = 80


def scan_secrets(root: Path, files: list[Path]) -> list[RawFinding]:
    findings: list[RawFinding] = []
    hits: list[tuple[str, str, int, bool]] = []  # (kind, relpath, line_no, high_conf)
    for f in files:
        rel = f.relative_to(root).as_posix()
        if rel.endswith((".env.example", ".env.sample", ".env.template")) or "test" in rel.lower():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for kind, rx, high_conf in SECRET_PATTERNS:
            for m in rx.finditer(text):
                line_text = text[max(0, m.start() - 80):m.end() + 80]
                if not high_conf and PLACEHOLDER.search(line_text):
                    continue  # looks like an example value, not a real credential
                line_no = text.count("\n", 0, m.start()) + 1
                hits.append((kind, rel, line_no, high_conf))
    for kind, rel, line_no, high_conf in hits[:20]:
        findings.append(RawFinding(
            category="security",
            severity="critical" if high_conf else "high",
            title=f"Possible hardcoded secret: {kind}",
            page_url=f"{rel}:{line_no}",
            description=f"A value matching the pattern for '{kind}' is committed at {rel}:{line_no}.",
            recommendation="Move secrets to environment variables or a secrets manager; rotate the credential if real.",
            element=f"{rel}:{line_no}",
            priority=1 if high_conf else 2,
        ))
    return findings


def scan_env_files(root: Path) -> list[RawFinding]:
    findings: list[RawFinding] = []
    for env in root.rglob(".env"):
        rel = env.relative_to(root).as_posix()
        if any(part in rel for part in ("node_modules", ".venv", "venv")):
            continue
        try:
            content = env.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        has_values = re.search(r"^\s*\w+\s*=\s*\S+", content, re.M)
        if has_values:
            findings.append(RawFinding(
                category="security",
                severity="high",
                title=f"Environment file committed: {rel}",
                page_url=rel,
                description="A .env file with values is present in the repository tree.",
                recommendation="Add .env to .gitignore and keep only a .env.example with empty values.",
                element=rel,
                priority=2,
            ))
    return findings[:5]


def scan_sizes(root: Path, line_counts: dict[str, int]) -> list[RawFinding]:
    findings: list[RawFinding] = []
    huge = sorted(((rel, n) for rel, n in line_counts.items() if n >= FILE_LINES_WARN),
                  key=lambda x: -x[1])
    for rel, n in huge[:10]:
        findings.append(RawFinding(
            category="maintainability",
            severity="medium" if n >= FILE_LINES_BAD else "low",
            title=f"Large file: {rel} ({n} lines)",
            page_url=rel,
            description="Very large files concentrate change risk and are hard to review.",
            recommendation="Split by responsibility (one concern per module).",
            element=rel,
            priority=3 if n >= FILE_LINES_BAD else 4,
        ))
    return findings


def scan_python_functions(root: Path, files: list[Path]) -> list[RawFinding]:
    findings: list[RawFinding] = []
    offenders: list[tuple[str, str, int]] = []
    for f in files:
        if f.suffix != ".py":
            continue
        rel = f.relative_to(root).as_posix()
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        text = "\n".join(lines)
        defs = [(m.start(), len(m.group(1).expandtabs(4)), m.group(2)) for m in PY_DEF.finditer(text)]
        for i, (start, indent, name) in enumerate(defs):
            start_line = text.count("\n", 0, start)
            end_line = len(lines)
            for j in range(start_line + 1, len(lines)):
                stripped = lines[j].strip()
                if stripped and (len(lines[j]) - len(lines[j].lstrip())) <= indent \
                        and not stripped.startswith(("#", '"', "'", ")", "]", "}")):
                    end_line = j
                    break
            length = end_line - start_line
            if length >= FUNC_LINES_WARN:
                offenders.append((rel, name, length))
    for rel, name, length in sorted(offenders, key=lambda x: -x[2])[:8]:
        findings.append(RawFinding(
            category="maintainability",
            severity="low",
            title=f"Long function: {name}() in {rel} (~{length} lines)",
            page_url=rel,
            description="Functions this long usually mix several responsibilities.",
            recommendation="Extract cohesive steps into smaller named functions.",
            element=f"{rel}::{name}",
            priority=4,
        ))
    return findings


def scan_todos(root: Path, files: list[Path]) -> tuple[list[RawFinding], int]:
    total = 0
    worst: list[tuple[str, int]] = []
    for f in files:
        rel = f.relative_to(root).as_posix()
        try:
            n = len(TODO_RX.findall(f.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
        if n:
            total += n
            worst.append((rel, n))
    findings: list[RawFinding] = []
    if total >= 10:
        worst.sort(key=lambda x: -x[1])
        findings.append(RawFinding(
            category="tech-debt",
            severity="info",
            title=f"{total} TODO/FIXME/HACK markers in the codebase",
            page_url=worst[0][0],
            description="High marker density indicates deferred work accumulating as debt.",
            recommendation="Triage into tracked issues; delete stale markers.",
            evidence={"top_files": [{"file": rel, "count": n} for rel, n in worst[:8]]},
            priority=5,
        ))
    return findings, total
