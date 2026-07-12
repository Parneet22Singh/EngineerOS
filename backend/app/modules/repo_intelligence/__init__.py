"""Module 2 — Universal Repository Intelligence.

Analyzes a local repository or GitHub URL: language/stack inventory, dependency
manifests, entry points, architecture structure map, internal import graph with
circular-dependency detection, dead-code candidates, code smells, and secret leaks.
"""
from app.modules.repo_intelligence.module import RepoIntelligenceModule

MODULE = RepoIntelligenceModule
