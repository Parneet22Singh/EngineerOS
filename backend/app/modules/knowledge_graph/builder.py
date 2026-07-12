"""Build the deterministic skeleton of the knowledge graph.

Reuses Repository Intelligence's inventory and import-graph analysis (no AI) to produce
nodes (source modules) and edges (import relationships), plus per-node connectivity
metrics used to decide which components are important enough to summarize with AI.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from app.modules.repo_intelligence import graph as graphmod
from app.modules.repo_intelligence.inventory import build_inventory

LANG_BY_EXT = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript", ".java": "Java", ".go": "Go", ".rb": "Ruby", ".rs": "Rust",
    ".php": "PHP", ".cs": "C#", ".c": "C", ".cpp": "C++", ".kt": "Kotlin",
}


@dataclass(slots=True)
class GraphNode:
    id: str                 # repo-relative path
    language: str
    lines: int
    in_degree: int          # how many modules import this one (fan-in / importance)
    out_degree: int         # how many modules this one imports (fan-out)
    summary: str = ""       # AI-filled later

    @property
    def degree(self) -> int:
        return self.in_degree + self.out_degree


@dataclass(slots=True)
class KnowledgeGraph:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)
    languages: list[dict] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)


def build_graph(root: Path, max_files: int = 5000) -> KnowledgeGraph:
    inv = build_inventory(root, max_files)
    import_graph = graphmod.build_import_graph(root, inv.files)  # relpath -> {relpaths}

    in_degree: dict[str, int] = defaultdict(int)
    out_degree: dict[str, int] = defaultdict(int)
    edges: list[tuple[str, str]] = []
    for src, targets in import_graph.items():
        out_degree[src] = len(targets)
        for tgt in targets:
            in_degree[tgt] += 1
            edges.append((src, tgt))

    # Node universe = every analyzable source file (so isolated files still appear).
    nodes: list[GraphNode] = []
    for f in inv.files:
        rel = f.relative_to(root).as_posix()
        nodes.append(GraphNode(
            id=rel,
            language=LANG_BY_EXT.get(f.suffix.lower(), f.suffix.lstrip(".") or "other"),
            lines=inv.line_counts.get(rel, 0),
            in_degree=in_degree.get(rel, 0),
            out_degree=out_degree.get(rel, 0),
        ))

    nodes.sort(key=lambda n: (n.degree, n.lines), reverse=True)
    return KnowledgeGraph(
        nodes=nodes,
        edges=edges,
        cycles=graphmod.find_cycles(import_graph),
        languages=inv.languages,
        frameworks=inv.frameworks,
        entry_points=inv.entry_points,
    )
