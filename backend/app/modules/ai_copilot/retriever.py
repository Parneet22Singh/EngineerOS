"""Lightweight code retrieval for grounding Copilot answers.

Keyword/overlap retrieval — no embeddings, no external index. Scores each source
file by how well the question's terms match its path and contents, then extracts the
most relevant snippets within a character budget so the prompt fits a small local
model's context window. Deterministic and dependency-free.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".next", "dist", "build",
    "out", "target", "vendor", ".idea", ".vscode", "coverage", ".pytest_cache",
    ".repos", "artifacts", ".mypy_cache", ".ruff_cache", ".ms-playwright",
}
TEXT_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rb", ".rs", ".php", ".cs",
    ".c", ".cpp", ".h", ".hpp", ".kt", ".swift", ".scala", ".sh", ".ps1", ".sql",
    ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".html", ".css",
}
MAX_FILE_BYTES = 400_000
# Machine-generated files: real strings, zero explanatory value — skip as context.
SKIP_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock",
    "composer.lock", "go.sum", "Gemfile.lock",
}
STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being", "do", "does",
    "did", "how", "what", "why", "where", "when", "which", "who", "whom", "this", "that",
    "these", "those", "it", "its", "of", "to", "in", "on", "for", "and", "or", "but",
    "with", "as", "by", "at", "from", "into", "about", "can", "could", "should", "would",
    "i", "you", "we", "they", "my", "our", "your", "me", "does", "use", "used", "using",
    "code", "file", "files", "function", "functions", "work", "works", "does", "there",
}
WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


@dataclass(slots=True)
class Snippet:
    path: str          # repo-relative
    text: str
    score: int


def _keywords(question: str) -> list[str]:
    seen: dict[str, None] = {}
    for w in WORD_RE.findall(question.lower()):
        if w not in STOPWORDS:
            seen.setdefault(w, None)
    return list(seen)


def _iter_files(root: Path, cap: int = 4000) -> list[Path]:
    out: list[Path] = []
    stack = [root]
    while stack and len(out) < cap:
        cur = stack.pop()
        try:
            for entry in cur.iterdir():
                if entry.is_dir():
                    if entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                        stack.append(entry)
                elif entry.suffix.lower() in TEXT_EXTS and entry.name not in SKIP_NAMES:
                    try:
                        if entry.stat().st_size <= MAX_FILE_BYTES:
                            out.append(entry)
                    except OSError:
                        continue
        except OSError:
            continue
    return out


def _best_window(lines: list[str], keywords: list[str], budget_chars: int) -> tuple[str, int]:
    """Slide over the file and pick the region with the densest keyword hits."""
    if not lines:
        return "", 0
    # Per-line hit counts.
    hits = []
    for ln in lines:
        low = ln.lower()
        hits.append(sum(low.count(k) for k in keywords))
    total = sum(hits)
    # Estimate a window of ~budget worth of lines around the densest area.
    avg_len = max(1, sum(len(ln) for ln in lines) // len(lines))
    win = max(20, min(len(lines), budget_chars // avg_len))
    if total == 0:
        # No keyword in body: return the head (useful for path-matched files).
        head = "\n".join(lines[:win])
        return head[:budget_chars], 0
    # Prefix sums to find the window with the most hits.
    best_start, best_sum = 0, -1
    running = sum(hits[:win])
    best_sum, best_start = running, 0
    for i in range(1, len(lines) - win + 1):
        running += hits[i + win - 1] - hits[i - 1]
        if running > best_sum:
            best_sum, best_start = running, i
    start = max(0, best_start - 2)
    end = min(len(lines), start + win)
    return "\n".join(lines[start:end])[:budget_chars], best_sum


def gather_context(
    root: Path, question: str, *, max_files: int = 6, char_budget: int = 8000
) -> tuple[str, list[str]]:
    """Return (context_block, source_paths) grounded in the repo for this question."""
    keywords = _keywords(question)
    if not keywords:
        return "", []

    scored: list[tuple[int, Path, str]] = []
    for f in _iter_files(root):
        rel = f.relative_to(root).as_posix()
        path_score = sum(3 for k in keywords if k in rel.lower())
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        low = text.lower()
        body_score = sum(low.count(k) for k in keywords)
        total = path_score + body_score
        if total > 0:
            scored.append((total, f, text))

    if not scored:
        return "", []

    scored.sort(key=lambda t: t[0], reverse=True)
    per_file = max(800, char_budget // max_files)
    blocks: list[str] = []
    sources: list[str] = []
    used = 0
    for total, f, text in scored[: max_files * 2]:
        if used >= char_budget or len(sources) >= max_files:
            break
        rel = f.relative_to(root).as_posix()
        window, _ = _best_window(text.splitlines(), keywords, per_file)
        if not window.strip():
            continue
        block = f"### {rel}\n```\n{window}\n```"
        blocks.append(block)
        sources.append(rel)
        used += len(block)
    return "\n\n".join(blocks), sources
